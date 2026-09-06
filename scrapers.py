import requests
import re
import json
import base64
from bs4 import BeautifulSoup
from urllib.parse import quote, urlparse, parse_qs, urljoin

# ---------------------------------------------------------------------------
# Shared session
# ---------------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

# Apply proxy from config (e.g. socks5://127.0.0.1:1080) so that CDN
# hosts blocked by the user's ISP can be reached through a local proxy/VPN.
try:
    from config import load_config, apply_proxy as _apply_proxy
    _apply_proxy(SESSION, load_config().get("proxy", ""))
except Exception as _e:
    print(f"[Scrapers] Could not apply proxy from config: {_e}")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

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


def safe_get(url, headers=None, timeout=15, referer=None, attempts=3):
    """GET with automatic SSL-fallback on certificate errors + retry."""
    import time as _time
    hdrs = headers if headers is not None else get_headers(referer=referer)
    last_exc = None
    for attempt in range(max(1, attempts)):
        try:
            return SESSION.get(url, headers=hdrs, timeout=timeout, allow_redirects=True)
        except requests.exceptions.SSLError:
            try:
                return SESSION.get(url, headers=hdrs, timeout=timeout, allow_redirects=True, verify=False)
            except Exception as e:
                last_exc = e
        except Exception as e:
            last_exc = e
        if attempt < attempts - 1:
            _time.sleep(min(1.0 * (2 ** attempt), 4.0))
    raise last_exc


def normalize_url(url, base_url=""):
    """Return an absolute URL, handling protocol-relative and relative paths."""
    if not url:
        return ""
    url = url.strip()
    if url.startswith("http"):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        # Strip path/query from base_url so we only keep the scheme+host
        parsed = urlparse(base_url)
        root = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else base_url.rstrip("/")
        return root + url
    return url


# ---------------------------------------------------------------------------
# Domain resolver (StreamingCommunity)
# ---------------------------------------------------------------------------

_BLOCKED_DOMAINS = frozenset({
    "streamingcommunityz.green",
    "streamingunity.club",
    "streamingunity.bike",
    "streamingcommunityz.buzz",
    "streamingcommunityz.eu",
})


def resolve_domain(domain):
    """Follow redirects to discover the currently active domain."""
    if any(b in domain for b in _BLOCKED_DOMAINS):
        domain = StreamingCommunityScraper.DEFAULT_DOMAIN
    try:
        r = safe_get(
            f"https://{domain}/",
            headers=get_headers(referer=f"https://{domain}/"),
            timeout=15,
        )
        host = urlparse(r.url).netloc
        if host and not any(b in host for b in _BLOCKED_DOMAINS):
            return host
    except Exception as e:
        print(f"[Domain Resolve] {e}")
    return domain


# ---------------------------------------------------------------------------
# VOE extractor
# ---------------------------------------------------------------------------

def _rot13(text):
    result = []
    for ch in text:
        if "A" <= ch <= "Z":
            result.append(chr((ord(ch) - ord("A") + 13) % 26 + ord("A")))
        elif "a" <= ch <= "z":
            result.append(chr((ord(ch) - ord("a") + 13) % 26 + ord("a")))
        else:
            result.append(ch)
    return "".join(result)


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


_VOE_ALIAS_HOSTS = [
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


def extract_voe_stream(voe_url):
    try:
        parsed = urlparse(voe_url)
        path_query = parsed.path + ("?" + parsed.query if parsed.query else "")
        urls_to_try = [voe_url] + [f"{host}{path_query}" for host in _VOE_ALIAS_HOSTS]

        html = ""
        for try_url in urls_to_try:
            try:
                r = SESSION.get(try_url, headers=get_headers(referer=voe_url), timeout=10)
                if r.status_code == 200:
                    html = r.text
                    break
            except Exception:
                continue

        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")
        json_script = soup.find("script", type="application/json")
        encoded_str = None
        if json_script:
            encoded_str = json_script.text.strip()
        else:
            m = re.search(r'<script\s+type="application/json">(.*?)</script>', html, re.DOTALL)
            if m:
                encoded_str = m.group(1).strip()

        if encoded_str:
            decrypted = decrypt_voe(encoded_str)
            if decrypted and "source" in decrypted:
                return decrypted["source"]

        print("[VOE] Falling back to regex search for m3u8...")
        m = re.search(r'["\']( https?://[^"\']+\.m3u8[^"\']*)["\']', html)
        if m:
            return m.group(1)
    except Exception as e:
        print(f"[VOE Extraction Error] {e}")
    return None


# ---------------------------------------------------------------------------
# Vixcloud extractor
# ---------------------------------------------------------------------------

def _sanitize_vixcloud_json(json_like):
    temp = json_like.replace("'", '"')
    return re.sub(r"\b(id|filename|token|expires|asn)\b\s*:", r'"\1":', temp)


def _find_video_script(soup):
    """Return the inline <script> block that contains window.video."""
    chunks = []
    for script in soup.find_all("script"):
        content = (script.string or script.get_text() or "").strip()
        if not content:
            continue
        if "window.video" in content or ("token" in content and "expires" in content):
            return content
        chunks.append(content)
    return "\n".join(chunks)


def _parse_vixcloud_from_script(script_text, embed_url, language=None):
    # --- Extract video id ---
    video_id = None
    for marker in ("window.video = ", "window.video="):
        if marker in script_text:
            raw = script_text.split(marker, 1)[1].split(";", 1)[0].strip()
            try:
                cleaned = _sanitize_vixcloud_json(raw)
                if not cleaned.startswith("{"):
                    cleaned = "{" + cleaned
                if not cleaned.endswith("}"):
                    cleaned = cleaned.rstrip(",") + "}"
                video_id = json.loads(cleaned).get("id")
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

    # --- Extract token / expires ---
    token = expires = None
    if "window.masterPlaylist" in script_text:
        block = script_text.split("window.masterPlaylist", 1)[1]
        if "params:" in block:
            params_raw = block.split("params:", 1)[1].split("},", 1)[0].strip().lstrip("{").rstrip("},")
            try:
                data = json.loads("{" + _sanitize_vixcloud_json(params_raw) + "}")
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

    # --- Fall back to query-string params ---
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

    if "b=1" in script_text or "b=1" in embed_url:
        playlist_url += "&b=1"
    if "canPlayFHD" in script_text or "canPlayFHD" in embed_url or qs.get("h", [None])[0] == "1":
        playlist_url += "&h=1"
    if language:
        playlist_url += f"&language={language}"

    return playlist_url


def embed_url_to_playlist(embed_url, language=None):
    """Build playlist URL from embed URL params without an HTTP request."""
    if "/playlist/" in embed_url:
        return embed_url
    return _parse_vixcloud_from_script("", embed_url, language)


def extract_vixcloud(embed_url, referer=None, language=None):
    """Port of VixcloudExtractor.kt — returns the m3u8 playlist URL."""
    try:
        quick = embed_url_to_playlist(embed_url, language)
        if quick:
            print(f"[Vixcloud] Playlist URL (from embed params): {quick}")
            return quick

        headers = get_headers(referer=referer or embed_url, language=language)
        r = safe_get(embed_url, headers=headers, timeout=15)
        if r.status_code != 200:
            print(f"[Vixcloud] HTTP {r.status_code} — embed unavailable")
            return None

        soup = BeautifulSoup(r.text, "html.parser")
        script_text = _find_video_script(soup)
        if not script_text:
            print("[Vixcloud] No inline script with video config found")
            return None

        playlist_url = _parse_vixcloud_from_script(script_text, embed_url, language)
        if playlist_url:
            print(f"[Vixcloud] Playlist URL (from page): {playlist_url}")
            return playlist_url

        print("[Vixcloud] Could not parse video id/token/expires")
    except Exception as e:
        print(f"[Vixcloud Extraction Error] {e}")
    return None


# ---------------------------------------------------------------------------
# Generic embed extractors
# ---------------------------------------------------------------------------

def extract_goodstream(link, referer=None):
    try:
        r = safe_get(link, headers=get_headers(referer=referer or link), timeout=15)
        if r.status_code != 200:
            return None
        for script in BeautifulSoup(r.text, "html.parser").find_all("script"):
            content = script.string or script.get_text() or ""
            if "jwplayer" in content and "file" in content:
                m = re.search(r'file\s*:\s*["\']([^"\']+)["\']', content)
                if m:
                    url = m.group(1).replace("\\/", "/")
                    print(f"[Goodstream] Found: {url}")
                    return url
    except Exception as e:
        print(f"[Goodstream Error] {e}")
    return None


def extract_generic_embed(link, referer=None):
    """Generic fallback for embed pages (Streamwish, Filemoon, etc.)."""
    try:
        if link.endswith((".m3u8", ".mp4")):
            return link
        r = safe_get(link, headers=get_headers(referer=referer or link), timeout=15)
        if r.status_code != 200:
            return None
        html = r.text
        patterns = [
            r'file\s*:\s*["\']([^"\']+)["\']',
            r'source\s*:\s*["\']([^"\']+)["\']',
            r'hls\s*:\s*["\']([^"\']+)["\']',
            r'["\']( https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'["\']( https?://[^"\']+\.mp4[^"\']*)["\']',
        ]
        for pattern in patterns:
            for m in re.finditer(pattern, html):
                url = m.group(1).replace("\\/", "/")
                if "youtube" not in url and "google" not in url:
                    print(f"[Generic] Found stream: {url}")
                    return url
    except Exception as e:
        print(f"[Generic Embed Error] {e}")
    return None


def try_extract_video_link(link, referer=None):
    """Dispatch to the right extractor based on the URL."""
    if not link:
        return None
    if link.endswith((".m3u8", ".mp4")) or "master.m3u8" in link:
        return link
    low = link.lower()
    if "voe" in low or "jilliandescribecompany.com" in low:
        return extract_voe_stream(link)
    if "goodstream" in low:
        return extract_goodstream(link, referer=referer)
    if "vixcloud" in low or "/embed/" in low:
        return extract_vixcloud(link, referer=referer)
    stream = extract_goodstream(link, referer=referer)
    if stream:
        return stream
    return extract_generic_embed(link, referer=referer)


# ---------------------------------------------------------------------------
# Altadefinizione scraper
# ---------------------------------------------------------------------------

class AltadefinizioneScraper:
    BASE_URL = "https://altadefinizione-01.study/"
    _POSTER_SKIP = ("no_image", "placeholder", "spacer", "blank", "1x1", "loading")

    @classmethod
    def _is_valid_poster_url(cls, url):
        if not url:
            return False
        low = url.lower()
        return not any(skip in low for skip in cls._POSTER_SKIP)

    @classmethod
    def _extract_poster(cls, item):
        candidates = []
        for img in item.select("a > img, .cover img, img"):
            for attr in ("data-src", "data-lazy-src", "data-original", "data-lazyload", "src"):
                url = img.get(attr, "").strip()
                if cls._is_valid_poster_url(url):
                    candidates.append(normalize_url(url, cls.BASE_URL))
        # Prefer non-GIF URLs
        for url in candidates:
            if url and not url.endswith(".gif"):
                return url
        return candidates[0] if candidates else ""

    @classmethod
    def search(cls, query):
        try:
            print(f"[Altadefinizione] Searching: {query}")
            params = {
                "do": "search",
                "subaction": "search",
                "titleonly": 3,
                "story": query,
                "full_search": 0,
            }
            r = SESSION.get(
                f"{cls.BASE_URL}/index.php",
                params=params,
                headers=get_headers(referer=cls.BASE_URL),
                timeout=15,
            )
            if r.status_code != 200:
                print(f"[Altadefinizione] Search returned HTTP {r.status_code}")
                return []

            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            for item in soup.select("#dle-content .boxgrid.caption"):
                anchor = item.select_one(".cover.boxcaption h2 a, h3 a, .boxcaption h2 a")
                if not anchor:
                    continue

                title = anchor.text.strip()
                href = anchor["href"].strip()

                is_tv = bool(item.select_one(".se_num"))
                if not is_tv:
                    for cat in item.select(".ml-cat a"):
                        if "serie-tv" in cat.get("href", "").lower() or "serie tv" in cat.text.lower():
                            is_tv = True
                            break

                results.append({
                    "title": title,
                    "url": href,
                    "poster": cls._extract_poster(item),
                    "referer": cls.BASE_URL,
                    "provider": "Altadefinizione (IT)",
                    "type": "tv" if is_tv else "movie",
                })
            return results

        except Exception as e:
            print(f"[Altadefinizione Search Error] {e}")
            return []

    @classmethod
    def get_stream_url(cls, movie_url):
        try:
            # Direct VidxGo TV episode API
            if "v.vidxgo.co/t/" in movie_url:
                print(f"[Altadefinizione] VidxGo TV episode: {movie_url}")
                r = SESSION.get(
                    movie_url,
                    headers=get_headers(referer=cls.BASE_URL),
                    timeout=15,
                )
                if r.status_code == 200:
                    try:
                        data = r.json()
                        url = data.get("url")
                        if url:
                            return url.replace("\\/", "/")
                    except Exception:
                        pass
                return None

            if "v.vidxgo.co" in movie_url:
                return cls._extract_vidxgo(movie_url, referer=cls.BASE_URL)

            if "voe" in movie_url or "jilliandescribecompany.com" in movie_url:
                return extract_voe_stream(movie_url)

            # --- Load the movie page ---
            print(f"[Altadefinizione] Loading movie page: {movie_url}")
            r = SESSION.get(movie_url, headers=get_headers(referer=cls.BASE_URL))
            html = r.text
            soup = BeautifulSoup(html, "html.parser")

            # Try VidxGo via embedded IMDB id
            imdb_id = cls._find_imdb_id(soup)
            if imdb_id:
                vidxgo_url = f"https://v.vidxgo.co/{imdb_id}"
                print(f"[Altadefinizione] VidxGo player: {vidxgo_url}")
                stream_url = cls._extract_vidxgo(vidxgo_url, referer=movie_url)
                if stream_url:
                    return stream_url

            # Try guardahd iframe
            guardahd_iframe = soup.find("iframe", src=re.compile(r"guardahd\.stream"))
            if guardahd_iframe:
                embed_url = normalize_url(guardahd_iframe["src"], cls.BASE_URL)
                print(f"[Altadefinizione] guardahd iframe: {embed_url}")
                r_embed = SESSION.get(embed_url, headers=get_headers(referer=movie_url))
                soup_embed = BeautifulSoup(r_embed.text, "html.parser")

                voe_link = first_link = None
                for li in soup_embed.select("ul._player-mirrors li[data-link]"):
                    link = li.get("data-link", "").strip()
                    if not link:
                        continue
                    link = normalize_url(link, "https://")
                    if not first_link:
                        first_link = link
                    if "voe" in li.text.lower() or "voe" in link:
                        voe_link = link
                        break

                target = voe_link or first_link
                if target:
                    print(f"[Altadefinizione] Mirror: {target}")
                    if "voe" in target:
                        return extract_voe_stream(target)
                    r_target = SESSION.get(target, headers=get_headers(referer=embed_url))
                    m = re.search(r'["\']( https?://[^"\']+\.m3u8[^"\']*)["\']', r_target.text)
                    if m:
                        return m.group(1)

            # Fallback: scan all iframes
            print("[Altadefinizione] Fallback: scanning all iframes...")
            for iframe in soup.find_all("iframe"):
                src = normalize_url(iframe.get("src", ""), cls.BASE_URL)
                if src:
                    print(f"[Altadefinizione Fallback] Trying iframe: {src}")
                    stream = try_extract_video_link(src, referer=movie_url)
                    if stream:
                        return stream

        except Exception as e:
            print(f"[Altadefinizione Stream Error] {e}")
        return None

    @classmethod
    def get_series_details(cls, series_url):
        try:
            print(f"[Altadefinizione] Fetching series: {series_url}")
            r = SESSION.get(series_url, headers=get_headers(referer=cls.BASE_URL))
            soup = BeautifulSoup(r.text, "html.parser")

            # --- Tab-based season layout ---
            tabs = soup.select("#tt_holder .tt_season ul li a[data-toggle=tab]")
            if tabs:
                return cls._parse_tab_seasons(soup, tabs)

            # --- VidxGo fallback ---
            imdb_id = cls._find_imdb_id(soup)
            if imdb_id:
                return cls._fetch_vidxgo_seasons(imdb_id, series_url)

            return []

        except Exception as e:
            print(f"[Altadefinizione Get Series Details Error] {e}")
            return []

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_imdb_id(soup):
        for script in soup.find_all("script"):
            m = re.search(r"var\s+imdb\s*=\s*['\"]tt(\d+)['\"]", script.text or "")
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _parse_tab_seasons(soup, tabs):
        seasons = []
        for tab in tabs:
            season_id = tab["href"].replace("#", "")
            try:
                season_num = int(tab.text.strip().replace("Stagione", "").strip())
            except ValueError:
                season_num = len(seasons) + 1

            pane = soup.find(id=season_id)
            episodes = []
            if pane:
                for ep in pane.select("ul > li > a[allowfullscreen][data-link]"):
                    ep_num_str = ep.get("data-num", "")
                    m = re.search(r"e(\d+)", ep_num_str.lower())
                    if m:
                        ep_num = int(m.group(1))
                    else:
                        try:
                            ep_num = int(ep_num_str)
                        except ValueError:
                            ep_num = len(episodes) + 1

                    ep_title = ep.get("data-title", f"Episodio {ep_num}").split(":")[-1].strip()
                    ep_link = normalize_url(ep.get("data-link", "").strip(), "https://")
                    episodes.append({"number": ep_num, "title": ep_title, "url": ep_link})

            seasons.append({
                "number": season_num,
                "episodes": sorted(episodes, key=lambda x: x["number"]),
            })
        return seasons

    @classmethod
    def _fetch_vidxgo_seasons(cls, imdb_id, series_url):
        vidxgo_url = f"https://v.vidxgo.co/{imdb_id}"
        print(f"[Altadefinizione] VidxGo series: {vidxgo_url}")
        try:
            r = SESSION.get(
                vidxgo_url,
                headers=get_headers(referer=series_url, extra={"sec-fetch-dest": "iframe"}),
                timeout=15,
            )
        except Exception as e:
            print(f"[Altadefinizione] VidxGo load failed: {e}")
            return []

        if r.status_code != 200:
            return []

        vix_soup = BeautifulSoup(r.text, "html.parser")
        season_tabs = vix_soup.select(".ep-season-tab")

        if season_tabs:
            return cls._fetch_vidxgo_season_tabs(season_tabs, imdb_id, vidxgo_url)
        return cls._parse_vidxgo_episode_list(vix_soup)

    @classmethod
    def _fetch_vidxgo_season_tabs(cls, season_tabs, imdb_id, vidxgo_url):
        seasons = []
        for tab in season_tabs:
            try:
                s_num = int(tab.get("data-season", ""))
            except (ValueError, TypeError):
                continue

            print(f"[Altadefinizione] VidxGo season {s_num}...")
            api_url = f"https://v.vidxgo.co/seasons.php?imdb={imdb_id}&season={s_num}"
            try:
                r_api = SESSION.get(
                    api_url,
                    headers=get_headers(referer=vidxgo_url, extra={"sec-fetch-dest": "empty"}),
                    timeout=15,
                )
                if r_api.status_code != 200:
                    continue
                data = r_api.json()
                if data.get("ok") != 1:
                    continue
                episodes = [
                    {
                        "number": ep["number"],
                        "title": ep.get("name") or f"Episodio {ep['number']}",
                        "url": f"https://v.vidxgo.co/t/{imdb_id}/{s_num}/{ep['number']}",
                    }
                    for ep in data.get("episodes", [])
                ]
                if episodes:
                    seasons.append({
                        "number": s_num,
                        "episodes": sorted(episodes, key=lambda x: x["number"]),
                    })
            except Exception as e:
                print(f"[Altadefinizione] Season {s_num} fetch error: {e}")
        return seasons

    @staticmethod
    def _parse_vidxgo_episode_list(vix_soup):
        seasons_map = {}
        for ep in vix_soup.select("#episodesList a.ep-item"):
            href = ep.get("href", "")
            parts = href.strip("/").split("/")
            if len(parts) < 3:
                continue
            try:
                s_num, e_num = int(parts[1]), int(parts[2])
            except ValueError:
                continue
            ep_name_el = ep.select_one(".ep-name")
            ep_title = ep_name_el.text.strip() if ep_name_el else f"Episodio {e_num}"
            seasons_map.setdefault(s_num, []).append({
                "number": e_num,
                "title": ep_title,
                "url": "https://v.vidxgo.co" + href,
            })
        return [
            {"number": s, "episodes": sorted(eps, key=lambda x: x["number"])}
            for s, eps in sorted(seasons_map.items())
        ]

    @classmethod
    def _extract_vidxgo(cls, vidxgo_url, referer):
        try:
            headers = get_headers(referer=referer, extra={"sec-fetch-dest": "iframe"})
            r = SESSION.get(vidxgo_url, headers=headers, timeout=10)
            html = r.text

            soup = BeautifulSoup(html, "html.parser")
            for script in soup.find_all("script"):
                text = (script.text or "").strip()
                k_match = re.search(r"var\s+k\s*=\s*['\"]([^'\"]+)['\"]", text)
                d_match = re.search(r"atob\(['\"]([^'\"]+)['\"]\)", text)
                if not (k_match and d_match):
                    continue
                k = k_match.group(1)
                try:
                    decoded = base64.b64decode(d_match.group(1))
                    decrypted = bytes(b ^ ord(k[i % len(k)]) for i, b in enumerate(decoded))
                    src_m = re.search(r"currentSrc\s*=\s*['\"]([^'\"]+)['\"]", decrypted.decode("utf-8", errors="ignore"))
                    if src_m:
                        return src_m.group(1).replace("\\/", "/")
                except Exception:
                    continue

            # Fallback: raw regex on the page
            m = re.search(r'["\']( https?://[^"\']+\.m3u8[^"\']*)["\']', html)
            if m:
                return m.group(1).replace("\\/", "/")

        except Exception as e:
            print(f"[VidxGo Decryption Error] {e}")
        return None
