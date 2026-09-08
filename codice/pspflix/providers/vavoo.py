"""Vavoo provider (ported from VavooProvider.kt).

Vavoo is an IPTV/live-TV provider backed by a JSON catalog
(mediahubmx-catalog.json / mediahubmx-resolve.json). This is a
faithful-but-pragmatic port: channels are surfaced as TV shows,
each with a single "Live" episode whose stream is resolved via
the resolve endpoint.
"""
import re
import json
from .base import Provider
from ..models import Movie, TvShow, Episode, Season, Genre, Category, Video, Server
from ..extractors import extract
from ..extractors._shared import safe_get, get_headers, normalize_url, SESSION, USER_AGENT
from bs4 import BeautifulSoup


class VavooProvider(Provider):
    supports_movies = False
    supports_tv_shows = True

    def __init__(self, language="de"):
        self.language = language
        self.base_url = "https://vavoo.to"
        self.name = f"Vavoo ({language.upper()})"
        self.logo = f"{self.base_url}/assets/favicon-Djqjt9PL.ico"
        self._catalog_url = f"{self.base_url}/mediahubmx-catalog.json"
        self._resolve_url = f"{self.base_url}/mediahubmx-resolve.json"
        self._groups = {
            "de": ["Germany"], "it": ["Italy"], "fr": ["France"],
            "es": ["Spain"], "pl": ["Poland"],
        }.get(language, ["Germany"])

    # ---- catalog ----------------------------------------------------------
    def _fetch_channels(self, search="", group=None, cursor=None):
        group = group or (self._groups[0] if self._groups else "")
        body = {
            "language": "de",
            "region": "AT",
            "catalogId": "iptv",
            "id": "iptv",
            "adult": False,
            "search": search or "",
            "sort": "name",
            "filter": {"group": group},
            "cursor": cursor,
            "clientVersion": "3.0.2",
        }
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json; charset=utf-8",
            "Origin": self.base_url,
            "Referer": self.base_url + "/",
        }
        try:
            r = SESSION.post(self._catalog_url, data=json.dumps(body),
                             headers=headers, timeout=20)
            data = r.json()
            items = data.get("items", []) if isinstance(data, dict) else []
            next_cursor = data.get("nextCursor") if isinstance(data, dict) else None
            channels = []
            for it in items:
                url = it.get("url") or it.get("id") or ""
                name = it.get("name") or it.get("title") or "Channel"
                logo = it.get("logo") or it.get("poster") or None
                if url:
                    channels.append({"url": url, "name": name, "logo": logo})
            return channels, next_cursor
        except Exception as e:
            print(f"[VavooProvider] catalog error: {e}")
            return [], None

    def _to_shows(self, channels):
        out = []
        for c in channels:
            out.append(TvShow(id=c["url"], title=c["name"], poster=c.get("logo")))
        return out

    # ---- discovery --------------------------------------------------------
    def get_home(self):
        return []

    def search(self, query, page=1):
        try:
            channels, _ = self._fetch_channels(search=query or "")
            return self._to_shows(channels)
        except Exception as e:
            print(f"[VavooProvider] search error: {e}")
            return []

    def get_movies(self, page=1):
        return []

    def get_tv_shows(self, page=1):
        try:
            cursor = None if page <= 1 else (page - 1) * 100
            channels, _ = self._fetch_channels(cursor=cursor)
            return self._to_shows(channels)
        except Exception as e:
            print(f"[VavooProvider] get_tv_shows error: {e}")
            return []

    def get_movie(self, item_id):
        return Movie(id=item_id, title=item_id)

    def get_tv_show(self, item_id):
        show = TvShow(id=item_id, title=item_id)
        show.seasons = [Season(id=f"{item_id}#s1", number=1, title="Live")]
        return show

    def get_episodes_by_season(self, season_id):
        show_id = season_id.split("#s")[0]
        return [Episode(id=show_id, number=1, title="Live")]

    # ---- playback ---------------------------------------------------------
    def _resolve(self, url):
        body = {
            "language": "de",
            "region": "AT",
            "url": url,
            "clientVersion": "3.0.2",
        }
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json; charset=utf-8",
            "Origin": self.base_url,
            "Referer": self.base_url + "/",
        }
        try:
            r = SESSION.post(self._resolve_url, data=json.dumps(body),
                             headers=headers, timeout=20)
            data = r.json()
            if isinstance(data, list) and data:
                return data[0].get("url", "")
            if isinstance(data, dict):
                return data.get("url", "")
        except Exception as e:
            print(f"[VavooProvider] resolve error: {e}")
        return ""

    def get_servers(self, item_id, video_type):
        resolved = self._resolve(item_id)
        src = resolved or item_id
        return [Server(id=src, name="Vavoo", src=src)]

    def get_video(self, server):
        src = server.src
        if src and src.endswith(".m3u8"):
            return Video(source=src, referer=self.base_url + "/",
                         headers={"User-Agent": USER_AGENT},
                         server_name="Vavoo")
        if not src or not src.startswith("http") or "vavoo.to" in src:
            resolved = self._resolve(src or server.id)
            if resolved:
                return Video(source=resolved, referer=self.base_url + "/",
                             headers={"User-Agent": USER_AGENT},
                             server_name="Vavoo")
        try:
            return extract(src, server)
        except Exception:
            return Video(source=src, referer=self.base_url + "/",
                         headers={"User-Agent": USER_AGENT},
                         server_name="Vavoo")
