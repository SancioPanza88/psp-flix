"""Vixcloud / vixcloud.co extractor (ported from scrapers.py).

Mirrors StreamFlix's VixcloudExtractor.kt — builds the m3u8
playlist URL from the embed page's window.video / token / expires.
"""
import re
from urllib.parse import urlparse, parse_qs
from .base import Extractor
from ..models import Video
from ._shared import SESSION, get_headers, safe_get

_VIX_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")


def _vix_headers(embed_url, cookies=None, language=None):
    """Build the exact headers StreamflixReborn's VixcloudExtractor sends to
    the player/downloader: Referer is the site root (scheme://host/), NOT the
    full embed URL. Using the embed URL as Referer triggers a 403."""
    parsed = urlparse(embed_url)
    root = f"{parsed.scheme or 'https'}://{parsed.netloc or 'vixcloud.co'}/"
    headers = {"Referer": root, "User-Agent": _VIX_UA}
    if language:
        headers["Accept-Language"] = (
            "en-US,en;q=0.9" if language == "en" else "it-IT,it;q=0.9")
        headers["Cookie"] = f"language={language}"
    if cookies:
        existing = headers.get("Cookie")
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers["Cookie"] = f"{existing}; {cookie_str}" if existing else cookie_str
    return headers


class VixcloudExtractor(Extractor):
    main_url = "vixcloud.co"
    name = "Vixcloud"
    alias_urls = ["vixcloud.club", "vixcloud.top"]

    def extract(self, embed_url: str, server=None) -> Video:
        lang = getattr(server, "language", None) if server else None
        headers = get_headers(referer=embed_url, language=lang)
        r = safe_get(embed_url, headers=headers, timeout=15)

        challenged = r.status_code != 200 or self._is_cf_challenge(r.text)
        if not challenged:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, "html.parser")
            script_text = self._find_video_script(soup)
            if script_text:
                playlist_url = self._parse(script_text, embed_url, language=lang)
                if playlist_url:
                    req_headers = _vix_headers(embed_url, language=lang)
                    good = self._validated(playlist_url, req_headers)
                    if good:
                        print(f"[Vixcloud] Playlist URL (from page): {good}")
                        return Video(source=good, headers=req_headers,
                                     referer=embed_url)
                    print("[Vixcloud] Page playlist invalid, trying browser/fallback...")

        # Cloudflare challenge / 403 => load the embed in a real browser.
        browser_video = self._extract_via_browser(embed_url, language=lang)
        if browser_video and browser_video.source:
            return browser_video

        # Last resort: build directly from the embed URL query params.
        quick = self._from_embed(embed_url, server)
        if quick:
            req_headers = _vix_headers(embed_url, language=lang)
            good = self._validated(quick, req_headers)
            if good:
                print(f"[Vixcloud] Playlist URL (from embed params): {good}")
                return Video(source=good, headers=req_headers,
                             referer=embed_url)

        print(f"[Vixcloud] HTTP {r.status_code} — could not extract playlist")
        return Video(source="")

    @staticmethod
    def _is_cf_challenge(html):
        if not html:
            return True
        markers = ("Just a moment", "Checking your browser",
                   "challenge-platform", "cf-browser-verification", "<title>Forbidden")
        return any(m in html for m in markers)

    def _extract_via_browser(self, embed_url, language=None):
        try:
            from . import _cf_session
        except Exception as e:
            print(f"[Vixcloud] browser helper unavailable: {e}")
            return None
        if not _cf_session.is_available():
            print("[Vixcloud] no Chrome browser available for CF bypass")
            return None
        html, cookies = _cf_session.fetch(embed_url)
        if not html:
            print("[Vixcloud] browser returned no HTML")
            return None
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        script_text = self._find_video_script(soup)
        if not script_text:
            print("[Vixcloud] browser page had no video script")
            return None
        playlist_url = self._parse(script_text, embed_url, language=language)
        if not playlist_url:
            print("[Vixcloud] browser page: could not parse id/token/expires")
            return None
        req_headers = _vix_headers(embed_url, cookies=cookies, language=language)
        good = self._validated(playlist_url, req_headers, cookies=cookies)
        if not good:
            print("[Vixcloud] browser playlist invalid")
            return None
        print(f"[Vixcloud] Playlist URL (via browser): {good}")
        return Video(source=good, headers=req_headers, referer=embed_url)

    # --- internals (ported from Kotlin) --------------------------------
    @staticmethod
    def _sanitize(json_like):
        temp = json_like.replace("'", '"')
        return re.sub(r"\b(id|filename|token|expires|asn)\b\s*:", r'"\1":', temp)

    def _find_video_script(self, soup):
        chunks = []
        for script in soup.find_all("script"):
            content = (script.string or script.get_text() or "").strip()
            if not content:
                continue
            if "window.video" in content or ("token" in content and "expires" in content):
                return content
            chunks.append(content)
        return "\n".join(chunks)

    def _parse(self, script_text, embed_url, language=None):
        video_id = None
        for marker in ("window.video = ", "window.video="):
            if marker in script_text:
                raw = script_text.split(marker, 1)[1].split(";", 1)[0].strip()
                try:
                    cleaned = self._sanitize(raw)
                    if not cleaned.startswith("{"):
                        cleaned = "{" + cleaned
                    if not cleaned.endswith("}"):
                        cleaned = cleaned.rstrip(",") + "}"
                    video_id = __import__("json").loads(cleaned).get("id")
                except Exception:
                    m = re.search(r'[\'"]id[\'"]\s*:\s*(\d+)', raw)
                    if m:
                        video_id = m.group(1)
                break

        if not video_id:
            for pattern in (r'id:\s*[\'"]?(\d+)[\'"]?', r'[\'"]id[\'"]\s*:\s*(\d+)'):
                m = re.search(pattern, script_text)
                if m:
                    video_id = m.group(1)
                    break

        token = expires = None
        if "window.masterPlaylist" in script_text:
            block = script_text.split("window.masterPlaylist", 1)[1]
            if "params:" in block:
                params_raw = block.split("params:", 1)[1].split("},", 1)[0].strip().lstrip("{").rstrip("},")
                try:
                    data = __import__("json").loads("{" + self._sanitize(params_raw) + "}")
                    token = data.get("token")
                    expires = data.get("expires")
                except Exception:
                    pass

        if not token:
            m = re.search(r'token:\s*["\']([^"\']+)["\']', script_text)
            if m:
                token = m.group(1)
        if not expires:
            m = re.search(r'expires:\s*["\']([^"\']+)["\']', script_text)
            if m:
                expires = m.group(1)

        parsed_embed = urlparse(embed_url)
        qs = parse_qs(parsed_embed.query)
        if not token:
            token = qs.get("token", [None])[0]
        if not expires:
            expires = qs.get("expires", [None])[0]
        if not video_id:
            parts = parsed_embed.path.strip("/").split("/")
            if parts:
                video_id = parts[-1]

        if not (video_id and token and expires):
            return None

        host = parsed_embed.netloc or "vixcloud.co"
        playlist_url = f"https://{host}/playlist/{video_id}?token={token}&expires={expires}"

        # Parità con VixcloudExtractor.kt upstream (i flag sbagliati = 403):
        # - b=1 solo se il campo 'url:' dello script lo contiene (non un
        #   'b=1' qualsiasi nella pagina/embed)
        # - h=1 solo se l'embed URL ha il param canPlayFHD (non se la
        #   stringa compare nel JS: la pagina la nomina sempre)
        # - lingua via param 'language='
        if re.search(r"url:\s*[\"'][^\"']*b=1", script_text):
            playlist_url += "&b=1"
        if qs.get("canPlayFHD") is not None:
            playlist_url += "&h=1"

        lang = language or qs.get("language", [None])[0] or qs.get("lang", [None])[0]
        if lang:
            playlist_url += f"&language={lang}"
        return playlist_url

    @staticmethod
    def _playlist_ok(url, headers, cookies=None):
        """Verifica che la playlist risponda davvero con un m3u8 (e non con
        una pagina di errore 403). Costa una GET ed evita 'Link error' e
        download falliti a valle."""
        try:
            from ._shared import SESSION
            r = SESSION.get(url, headers=headers, cookies=cookies or {}, timeout=15)
            return (r.status_code == 200 and len(r.content) > 100
                    and ("#EXTM3U" in r.text or "#EXT-X" in r.text))
        except Exception:
            return False

    def _validated(self, url, headers, cookies=None):
        """Ritorna url se valido, altrimenti prova varianti degradate
        (senza b/h/language, poi solo token+expires): vixcloud rifiuta con
        403 i flag non supportati dal singolo video."""
        if self._playlist_ok(url, headers, cookies):
            return url
        print(f"[Vixcloud] Playlist rejected (403?), trying fallback variants...")
        from urllib.parse import urlparse as _up, parse_qs as _pqs
        base = url.split("?")[0]
        q = _pqs(_up(url).query)
        tok, exp = (q.get("token", [""])[0], q.get("expires", [""])[0])
        lang = q.get("language", [None])[0]
        for variant in (
            f"{base}?token={tok}&expires={exp}&language={lang}" if lang else None,
            f"{base}?token={tok}&expires={exp}",
        ):
            if variant and self._playlist_ok(variant, headers, cookies):
                print(f"[Vixcloud] Fallback playlist OK: {variant[-40:]}")
                return variant
        return None

    def _from_embed(self, embed_url, server):
        if "/playlist/" in embed_url:
            return embed_url
        # If the embed URL already carries token+expires (as vixcloud.co does),
        # build the playlist directly and skip the Cloudflare-protected page.
        qs = parse_qs(urlparse(embed_url).query)
        if qs.get("token") and qs.get("expires"):
            return self._parse("", embed_url)
        return None
