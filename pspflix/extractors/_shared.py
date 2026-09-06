"""Shared HTTP session, headers and helpers for PSPflix extractors/providers.

Mirrors the combined behaviour of StreamFlix's NetworkClient /
Jsoup usage: a global requests.Session with a desktop User-Agent,
optional proxy from psp_config.json, and SSL-fallback GET.
"""
import os
import re
import json
import time
import base64
import threading
from urllib.parse import quote, urlparse, parse_qs, urljoin

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


# ---------------------------------------------------------------------------
# DNS-over-HTTPS resolver (ported from Android DnsResolver.kt).
#
# Some provider domains (sflix.to, vixcloud, altadefinizione, cuevana) are
# blocked/poisoned by local ISP DNS. We resolve their A records through a
# DoH endpoint (Cloudflare by default) and pin the resolved IP for the TCP
# connection while keeping the original Host/SNI, mirroring OkHttp's
# DnsOverHttps behaviour.
# ---------------------------------------------------------------------------
DEFAULT_DOH_PROVIDER_URL = "https://cloudflare-dns.com/dns-query"

_doh_url = DEFAULT_DOH_PROVIDER_URL
_doh_cache = {}
_doh_lock = threading.Lock()
_doh_failures = 0  # consecutive DoH endpoint failures (circuit-breaker)
_doh_client = requests.Session()
_doh_client.headers.update({"User-Agent": USER_AGENT})


def set_doh_url(url):
    """Change the DoH provider (empty string => system DNS)."""
    global _doh_url, _doh_failures
    with _doh_lock:
        _doh_url = url or ""
        _doh_failures = 0
        _doh_cache.clear()


def _is_doh_host(hostname):
    """True if hostname belongs to the DoH provider (avoid recursion)."""
    if not _doh_url:
        return False
    try:
        doh_host = urlparse(_doh_url).hostname
    except Exception:
        return False
    return bool(doh_host) and hostname.lower() == doh_host.lower()


def _doh_lookup(hostname):
    """Return a list of IPv4 addresses for hostname via DoH (cached).

    Circuit-breaker: se l'endpoint DoH non risponde per 3 volte di fila
    (rete che lo blocca), DoH viene disabilitato per la sessione e si usa
    il DNS di sistema — altrimenti ogni nuovo host pagherebbe il timeout
    a vuoto e tutto sembrerebbe 'rotto'.
    """
    global _doh_url, _doh_failures
    if not _doh_url or _is_doh_host(hostname):
        return []
    now = time.time()
    with _doh_lock:
        cached = _doh_cache.get(hostname)
        if cached and cached[1] > now:
            return cached[0]
    try:
        r = _doh_client.get(
            _doh_url,
            params={"name": hostname, "type": "A"},
            headers={"Accept": "application/dns-json"},
            timeout=4,
        )
        data = r.json()
        ips = [a["data"] for a in data.get("Answer", [])
               if a.get("type") == 1 and _is_ipv4(a.get("data", ""))]
        with _doh_lock:
            _doh_failures = 0
            if ips:
                _doh_cache[hostname] = (ips, now + 300)
        return ips
    except Exception as e:
        print(f"[DoH] lookup failed for {hostname}: {e}")
        with _doh_lock:
            _doh_failures += 1
            if _doh_failures >= 3 and _doh_url:
                print("[DoH] Endpoint irraggiungibile, passo al DNS di sistema.")
                _doh_url = ""
        return []


def _is_ipv4(value):
    parts = value.split(".")
    if len(parts) != 4:
        return False
    return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


try:
    from urllib3.util import connection as _u3_connection

    _orig_create_connection = _u3_connection.create_connection

    def _doh_create_connection(address, *args, **kwargs):
        host, port = address
        if host and not _is_ipv4(host) and not _is_doh_host(host):
            ips = _doh_lookup(host)
            if ips:
                return _orig_create_connection((ips[0], port), *args, **kwargs)
        return _orig_create_connection(address, *args, **kwargs)

    _u3_connection.create_connection = _doh_create_connection
    _DOH_AVAILABLE = True
except Exception as _e:  # pragma: no cover
    print(f"[DoH] could not install resolver hook: {_e}")
    _DOH_AVAILABLE = False


# ---- proxy (from psp_config.json if present) ----------------------------
def _load_proxy():
    try:
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "..", "psp_config.json")
        with open(os.path.abspath(cfg_path), "r", encoding="utf-8") as f:
            cfg = json.load(f)
        proxy = cfg.get("proxy", "")
        if proxy:
            SESSION.proxies.update({"http": proxy, "https": proxy})
    except Exception:
        pass


_load_proxy()


def get_headers(referer=None, extra=None, language=None):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    if language == "en":
        headers["Accept-Language"] = "en-US,en;q=0.9"
        headers["Cookie"] = "language=en"
    elif language == "it":
        headers["Accept-Language"] = "it-IT,it;q=0.9"
        headers["Cookie"] = "language=it"
    elif language == "es":
        headers["Accept-Language"] = "es-ES,es;q=0.9"
    if referer:
        headers["Referer"] = referer
    if extra:
        headers.update(extra)
    return headers


def safe_get(url, headers=None, timeout=15, referer=None, params=None, attempts=3):
    """GET con retry + backoff e SSL-fallback sugli errori di certificato.

    Senza retry, un singolo reset transitorio (molto frequente su questi
    host: RemoteDisconnected/ConnectionReset) svuotava ricerche e server
    ('No results' / 'Link error' ingiustificati).
    """
    hdrs = headers if headers is not None else get_headers(referer=referer)
    last_exc = None
    for attempt in range(max(1, attempts)):
        try:
            return SESSION.get(url, headers=hdrs, timeout=timeout,
                               allow_redirects=True, params=params)
        except requests.exceptions.SSLError:
            try:
                return SESSION.get(url, headers=hdrs, timeout=timeout,
                                   allow_redirects=True, verify=False,
                                   params=params)
            except Exception as e:
                last_exc = e
        except Exception as e:
            last_exc = e
        if attempt < attempts - 1:
            time.sleep(min(1.0 * (2 ** attempt), 4.0))
    raise last_exc


def normalize_url(url, base_url=""):
    if not url:
        return ""
    url = url.strip()
    if url.startswith("http"):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        parsed = urlparse(base_url)
        root = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else base_url.rstrip("/")
        return root + url
    return url


# ---- VOE decoder (ported from scrapers.py) -----------------------------
def _rot13(text):
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr((ord(ch) - ord("A") + 13) % 26 + ord("A")))
        elif "a" <= ch <= "z":
            out.append(chr((ord(ch) - ord("a") + 13) % 26 + ord("a")))
        else:
            out.append(ch)
    return "".join(out)


def _pad_base64(s):
    missing = len(s) % 4
    return s + "=" * (4 - missing) if missing else s


def decrypt_voe(encoded_str):
    try:
        v = _rot13(encoded_str)
        for pattern in ("@$", "^^", "~@", "%?", "*~", "!!", "#&"):
            v = v.replace(pattern, "")
        v = v.replace("_", "")
        v_decoded = base64.b64decode(_pad_base64(v)).decode("utf-8")
        v_shifted = "".join(chr(ord(c) - 3) for c in v_decoded)
        v_reversed = v_shifted[::-1]
        v_final = base64.b64decode(_pad_base64(v_reversed)).decode("utf-8")
        return json.loads(v_final)
    except Exception as e:
        print(f"[VOE Decrypt Error] {e}")
        return None


VOE_ALIAS_HOSTS = [
    "https://jilliandescribecompany.com",
    "https://mikaylaarealike.com",
    "https://christopheruntilpoint.com",
    "https://walterprettytheir.com",
    "https://crystaltreatmenteast.com",
    "https://lauradaydo.com",
    "https://lancewhosedifficult.com",
    "https://dianaavoidthey.com",
    "https://jefferycontrolmodel.com",
    "https://charlestoughrace.com",
    "https://richardquestionbuilding.com",
    "https://jessicayeahcatch.com",
]
