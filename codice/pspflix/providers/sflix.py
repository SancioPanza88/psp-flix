"""SFlix provider (ported from SflixProvider.kt).

Uses sflix.to's HTML pages plus its AJAX endpoints for seasons,
episodes and server links. Movie vs TvShow is decided exactly like
the Android app: an item is a Movie iff its <a> href contains "/movie/".
"""
import re
from .base import Provider
from ..models import Movie, TvShow, Episode, Season, Genre, Category, Video, Server
from ..extractors import extract
from ..extractors._shared import safe_get, get_headers, normalize_url
from bs4 import BeautifulSoup


class SflixProvider(Provider):
    base_url = "https://sflix.to"
    name = "SFlix"
    language = "en"
    supports_movies = True
    supports_tv_shows = True

    # ---- helpers ----------------------------------------------------------
    def _get(self, url, referer=None):
        return safe_get(url, headers=get_headers(referer=referer or self.base_url,
                                                 language=self.language), timeout=30)

    def _soup(self, url, referer=None):
        return BeautifulSoup(self._get(url, referer=referer).text, "html.parser")

    def _norm(self, url):
        return normalize_url(url or "", self.base_url)

    @staticmethod
    def _numerical_id(item_id):
        return (item_id or "").rstrip("/").rsplit("-", 1)[-1]

    @staticmethod
    def _is_movie(el):
        a = el.select_one("a")
        href = a.get("href", "") if a else ""
        return "/movie/" in href

    def _map_item(self, el, name_sel="h2.film-name"):
        a = el.select_one("a")
        href = self._norm(a.get("href", "")) if a else ""
        name = el.select_one(name_sel)
        title = name.get_text(strip=True) if name else ""
        img = el.select_one("div.film-poster > img.film-poster-img") or el.select_one("img")
        poster = self._norm(img.get("data-src") or img.get("src")) if img else None
        if not href or not title:
            return None
        if self._is_movie(el):
            return Movie(id=href, title=title, poster=poster)
        return TvShow(id=href, title=title, poster=poster)

    def _parse_cards(self, soup, name_sel="h2.film-name"):
        out = []
        for el in soup.select("div.flw-item"):
            item = self._map_item(el, name_sel)
            if item:
                out.append(item)
        return out

    # ---- discovery --------------------------------------------------------
    def get_home(self):
        return []

    def search(self, query, page=1):
        if not query:
            return []
        try:
            q = query.strip().replace(" ", "-")
            url = f"{self.base_url}/search/{q}?page={page}"
            return self._parse_cards(self._soup(url))
        except Exception as e:
            print(f"[SFlix] search error: {e}")
            return []

    def get_movies(self, page=1):
        try:
            return [x for x in self._parse_cards(self._soup(f"{self.base_url}/movie?page={page}"))
                    if x.type == "movie"]
        except Exception as e:
            print(f"[SFlix] get_movies error: {e}")
            return []

    def get_tv_shows(self, page=1):
        try:
            return [x for x in self._parse_cards(self._soup(f"{self.base_url}/tv-show?page={page}"))
                    if x.type == "tv"]
        except Exception as e:
            print(f"[SFlix] get_tv_shows error: {e}")
            return []

    # ---- details ----------------------------------------------------------
    def get_movie(self, item_id):
        try:
            soup = self._soup(item_id, referer=item_id)
            title = soup.select_one("h2.heading-name")
            poster = soup.select_one("div.detail_page-watch img.film-poster-img")
            return Movie(id=item_id,
                         title=title.get_text(strip=True) if title else "Unknown",
                         poster=self._norm(poster.get("src")) if poster else None)
        except Exception as e:
            print(f"[SFlix] get_movie error: {e}")
            return Movie(id=item_id, title="Unknown")

    def get_tv_show(self, item_id):
        try:
            soup = self._soup(item_id, referer=item_id)
            title = soup.select_one("h2.heading-name")
            poster = soup.select_one("div.detail_page-watch img.film-poster-img")
            show = TvShow(id=item_id,
                          title=title.get_text(strip=True) if title else "Unknown",
                          poster=self._norm(poster.get("src")) if poster else None)
        except Exception as e:
            print(f"[SFlix] get_tv_show error: {e}")
            show = TvShow(id=item_id, title="Unknown")
            show.seasons = []
            return show

        seasons = []
        try:
            url = f"{self.base_url}/ajax/season/list/{self._numerical_id(item_id)}"
            ssoup = self._soup(url, referer=item_id)
            for i, a in enumerate(ssoup.select("div.dropdown-menu.dropdown-menu-model > a")):
                seasons.append(Season(id=a.get("data-id", ""), number=i + 1,
                                      title=a.get_text(strip=True)))
        except Exception as e:
            print(f"[SFlix] seasons error: {e}")
        show.seasons = seasons
        return show

    def get_episodes_by_season(self, season_id):
        try:
            url = f"{self.base_url}/ajax/season/episodes/{season_id}"
            soup = self._soup(url, referer=self.base_url)
            eps = []
            sel = "div.flw-item.film_single-item.episode-item.eps-item"
            for i, el in enumerate(soup.select(sel)):
                numtxt = el.select_one("div.episode-number")
                num = i + 1
                if numtxt:
                    m = re.search(r"Episode\s+(\d+)", numtxt.get_text())
                    if m:
                        num = int(m.group(1))
                name = el.select_one("h3.film-name")
                img = el.select_one("img")
                eps.append(Episode(id=el.get("data-id", ""), number=num,
                                   title=name.get_text(strip=True) if name else None,
                                   poster=img.get("src") if img else None))
            return eps
        except Exception as e:
            print(f"[SFlix] episodes error: {e}")
            return []

    # ---- playback ---------------------------------------------------------
    def get_servers(self, item_id, video_type):
        try:
            is_ep = video_type == "episode"
            if is_ep:
                url = f"{self.base_url}/ajax/episode/servers/{item_id}"
            else:
                url = f"{self.base_url}/ajax/episode/list/{self._numerical_id(item_id)}"
            soup = self._soup(url, referer=self.base_url)
            servers = []
            for a in soup.select("a"):
                sid = a.get("data-id")
                if not sid:
                    continue
                span = a.select_one("span")
                name = span.get_text(strip=True) if span else (a.get_text(strip=True) or "Server")
                servers.append(Server(id=sid, name=name, src=""))
            return servers
        except Exception as e:
            print(f"[SFlix] servers error: {e}")
            return []

    def get_video(self, server):
        try:
            url = f"{self.base_url}/ajax/episode/sources/{server.id}"
            r = self._get(url, referer=self.base_url)
            data = r.json()
            link = data.get("link", "")
            if not link:
                return Video(source="")
            return extract(link, server)
        except Exception as e:
            print(f"[SFlix] video error: {e}")
            return Video(source="")
