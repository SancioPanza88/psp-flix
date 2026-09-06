"""StreamingCommunity provider (ported from StreamingCommunityProvider.kt).

Supports both Italian and English via the `language` constructor arg,
exactly like the Android dual-instance provider.
"""
import re
import json
from urllib.parse import urljoin
from .base import Provider
from ..models import Movie, TvShow, Episode, Season, Genre, Category, Video, Server
from ..extractors import VixcloudExtractor
from ..extractors._shared import safe_get, get_headers, normalize_url


DEFAULT_DOMAIN = "streamingunity.cc"
BLOCKED_DOMAINS = {
    "streamingcommunityz.green", "streamingunity.club",
    "streamingunity.bike", "streamingcommunityz.buzz",
    "streamingcommunityz.eu",
}


class StreamingCommunityProvider(Provider):
    def __init__(self, language=None):
        self._language = language
        self._domain = None
        self._version = ""

    @property
    def language(self):
        return self._language or "it"

    @property
    def base_url(self):
        return self.domain

    @property
    def domain(self):
        if self._domain:
            return self._domain
        stored = self._load_domain()
        if not stored or any(b in stored for b in BLOCKED_DOMAINS):
            self._domain = DEFAULT_DOMAIN
        else:
            self._domain = stored
        return self._domain

    @domain.setter
    def domain(self, value):
        self._domain = value
        self._save_domain(value)

    @property
    def name(self):
        return "StreamingCommunity" if self.language == "it" else "StreamingCommunity (EN)"

    @property
    def logo(self):
        return f"https://{self.domain}/apple-touch-icon.png"

    supports_movies = True
    supports_tv_shows = True

    # --- domain persistence (lightweight) --------------------------------
    def _cfg_path(self):
        import os
        return os.path.abspath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
            "psp_config.json"))

    def _load_domain(self):
        try:
            import json as _j
            with open(self._cfg_path(), "r", encoding="utf-8") as f:
                return _j.load(f).get("streamingcommunity_domain", "")
        except Exception:
            return ""

    def _save_domain(self, value):
        try:
            import json as _j, os
            p = self._cfg_path()
            cfg = {}
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    cfg = _j.load(f)
            cfg["streamingcommunity_domain"] = value
            with open(p, "w", encoding="utf-8") as f:
                _j.dump(cfg, f, indent=2)
        except Exception:
            pass

    def _lang(self):
        return "en" if self.language == "en" else "it"

    def _resolve_base(self):
        try:
            r = safe_get(f"https://{self.domain}/",
                         headers=get_headers(referer=f"https://{self.domain}/"),
                         timeout=15)
            host = urljoin(r.url, "/").rstrip("/").split("//")[-1]
            if host and not any(b in host for b in BLOCKED_DOMAINS):
                self.domain = host
                return f"https://{host}/"
        except Exception as e:
            print(f"[SC] Resolve error: {e}")
        return f"https://{self.domain}/"

    def _img(self, filename):
        if not filename:
            return None
        return f"https://cdn.{self.domain}/images/{filename}"

    def _find_poster(self, images):
        if not images:
            return None
        for img in images:
            if img.get("type") == "poster":
                return self._img(img.get("filename"))
        return None

    def _map_show(self, s):
        """Map a JSON Show dict to a Movie or TvShow (respecting `type`)."""
        sid = f"{s.get('id')}-{s.get('slug')}"
        poster = self._find_poster(s.get("images"))
        rating = None
        try:
            rating = float(s["score"]) if s.get("score") else None
        except (ValueError, TypeError):
            rating = None
        common = dict(id=sid, title=s.get("name"), poster=poster,
                      released=s.get("last_air_date"), rating=rating)
        if s.get("type") == "movie":
            return Movie(**common)
        return TvShow(**common)

    # --- discovery --------------------------------------------------------
    def get_home(self):
        return []

    def search(self, query, page=1):
        if not query:
            return []
        try:
            url = f"https://{self.domain}/{self._lang()}/search"
            r = safe_get(url, params={"q": query, "page": page, "lang": self._lang()},
                         headers=get_headers(language=self.language,
                                             extra={"Accept": "application/json, text/plain, */*"}),
                         timeout=15)
            data = r.json()
            current = data.get("current_page")
            last = data.get("last_page")
            if current is None or (last is not None and current > last):
                return []
            return [self._map_show(s) for s in data.get("data", [])]
        except Exception as e:
            print(f"[SC] search error: {e}")
            return []

    def get_movies(self, page=1):
        return self._archive("movie", page)

    def get_tv_shows(self, page=1):
        return self._archive("tv", page)

    def _archive(self, kind, page):
        try:
            offset = (page - 1) * 60
            url = f"https://{self.domain}/api/archive"
            r = safe_get(url, params={"lang": self._lang(), "offset": offset, "type": kind},
                         headers=get_headers(language=self.language,
                                             extra={"Accept": "application/json, text/plain, */*"}),
                         timeout=15)
            data = r.json()
            return [self._map_show(s) for s in data.get("titles", [])]
        except Exception as e:
            print(f"[SC] archive error: {e}")
            return []

    def get_movie(self, item_id):
        s = self._details(item_id)
        if not s:
            return Movie(id=item_id, title="Errore")
        return self._map_show(dict(s, type="movie"))

    def get_tv_show(self, item_id):
        s = self._details(item_id)
        if not s:
            show = TvShow(id=item_id, title="Errore")
            show.seasons = [Season(id=f"{item_id}/season-1", number=1, title="Stagione 1")]
            return show
        show = self._map_show(dict(s, type="tv"))
        seasons = []
        for i, se in enumerate(s.get("seasons") or []):
            num_raw = str(se.get("number", i + 1))
            try:
                num = int(num_raw)
            except ValueError:
                num = i + 1
            seasons.append(Season(id=f"{item_id}/season-{num_raw}", number=num,
                                   title=se.get("name") or f"Stagione {num}"))
        if not seasons:
            seasons.append(Season(id=f"{item_id}/season-1", number=1, title="Stagione 1"))
        show.seasons = seasons
        return show

    def _parse_inertia_html(self, url):
        """Fetch a page and extract the Inertia JSON from the data-page
        attribute (fallback when the JSON endpoint returns HTML)."""
        import html as _html
        r = safe_get(url, headers=get_headers(referer=f"https://{self.domain}/",
                                              language=self.language), timeout=15)
        m = re.search(r'data-page="([^"]+)"', r.text)
        if not m:
            return None
        raw = _html.unescape(m.group(1))
        return json.loads(raw)

    def _details(self, item_id):
        """Fetch title details JSON. Returns the `title` dict from props,
        or None on failure. Falls back to parsing the Inertia HTML page."""
        version = self._ensure_version()
        try:
            url = f"https://{self.domain}/{self._lang()}/titles/{item_id}"
            r = safe_get(url, params={"lang": self._lang()},
                         headers=get_headers(language=self.language, extra={
                             "x-inertia": "true",
                             "x-inertia-version": version,
                             "X-Requested-With": "XMLHttpRequest",
                             "Accept": "application/json, text/plain, */*",
                         }), timeout=15)
            data = r.json()
        except Exception:
            try:
                data = self._parse_inertia_html(
                    f"https://{self.domain}/{self._lang()}/titles/{item_id}")
            except Exception as e:
                print(f"[SC] details error: {e}")
                return None
        if not data:
            return None
        new_v = data.get("version")
        if new_v:
            self._version = new_v
        return (data.get("props") or {}).get("title")

    def _ensure_version(self):
        if self._version:
            return self._version
        try:
            r = safe_get(f"https://{self.domain}/{self._lang()}/",
                         headers=get_headers(language=self.language, extra={
                             "x-inertia": "true",
                             "X-Requested-With": "XMLHttpRequest",
                             "Accept": "application/json, text/plain, */*",
                         }), timeout=15)
            self._version = r.json().get("version") or ""
        except Exception:
            self._version = ""
        return self._version

    def get_episodes_by_season(self, season_id):
        version = self._ensure_version()
        try:
            url = f"https://{self.domain}/{self._lang()}/titles/{season_id}/"
            try:
                r = safe_get(url, params={"lang": self._lang()},
                             headers=get_headers(language=self.language, extra={
                                 "x-inertia": "true",
                                 "x-inertia-version": version,
                                 "X-Requested-With": "XMLHttpRequest",
                                 "Accept": "application/json, text/plain, */*",
                             }), timeout=15)
                data = r.json()
            except Exception:
                data = self._parse_inertia_html(url) or {}
            new_v = data.get("version")
            if new_v:
                self._version = new_v
            loaded = ((data.get("props") or {}).get("loadedSeason") or {})
            episodes = loaded.get("episodes") or []
            base_id = season_id.split("-")[0]
            eps = []
            for i, ep in enumerate(episodes):
                try:
                    num = int(ep.get("number", i + 1))
                except (ValueError, TypeError):
                    num = i + 1
                cover = None
                for img in ep.get("images") or []:
                    if img.get("type") == "cover":
                        cover = self._img(img.get("filename"))
                        break
                eps.append(Episode(id=f"{base_id}?episode_id={ep.get('id')}",
                                   number=num, title=ep.get("name"),
                                   poster=cover, overview=ep.get("plot")))
            return eps
        except Exception as e:
            print(f"[SC] episodes error: {e}")
            return []

    # --- playback --------------------------------------------------------
    def _iframe_url(self, item_id):
        base = f"https://{self.domain}/"
        lang = self._lang()
        if "?episode_id=" in item_id:
            title_id = item_id.split("?")[0]
            ep_id = item_id.split("episode_id=")[-1].split("&")[0]
            return (base + f"{lang}/iframe/" + title_id
                    + "?episode_id=" + ep_id + "&next_episode=1"
                    + f"&language={lang}")
        return base + f"{lang}/iframe/" + item_id.split("-")[0] + f"?language={lang}"

    def get_servers(self, item_id, video_type):
        base = f"https://{self.domain}/"
        iframe = self._iframe_url(item_id)
        try:
            doc = safe_get(iframe, headers=get_headers(referer=base, language=self.language), timeout=15)
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(doc.text, "html.parser")
            src = soup.select_one("iframe")
            src = src.get("src") if src else ""
            if src:
                srv = Server(id=item_id, name="Vixcloud", src=normalize_url(src, base))
                try:
                    srv.language = self._lang()
                except Exception:
                    pass
                return [srv]
        except Exception as e:
            print(f"[SC] servers error: {e}")
        return []

    def get_video(self, server):
        try:
            video = VixcloudExtractor().extract(server.src, server)
            if video and video.source:
                return video
        except Exception as e:
            print(f"[SC] video error: {e}")
        # Token vixcloud scaduto (410 / stream vuoto): re-fetcha l'iframe
        # per ottenere token+expires freschi e riprova una volta, come fa
        # l'app Android upstream.
        try:
            print("[SC] Empty stream, re-fetching iframe (expired token?)...")
            servers = self.get_servers(getattr(server, "id", ""), "")
            if servers:
                return VixcloudExtractor().extract(servers[0].src, servers[0])
        except Exception as e:
            print(f"[SC] video retry error: {e}")
        return Video(source="")
