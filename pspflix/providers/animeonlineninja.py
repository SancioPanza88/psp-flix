"""AnimeOnlineNinja provider (ported from AnimeOnlineNinjaProvider.kt).

Faithful-but-pragmatic port for PSPflix. Scraping is defensive:
network/parse failures return empty results instead of raising.
"""
import re
import json
from .base import Provider
from ..models import Movie, TvShow, Episode, Season, Genre, Category, Video, Server
from ..extractors import extract
from ..extractors._shared import safe_get, get_headers, normalize_url
from bs4 import BeautifulSoup


class AnimeOnlineNinjaProvider(Provider):
    base_url = "https://ww3.animeonline.ninja"
    name = "AnimeOnlineNinja"
    language = "it"
    supports_movies = True
    supports_tv_shows = True

    search_path = "{base}/?s={q}"
    movies_path = "{base}/pelicula/page/{page}"
    tv_path = "{base}/serie/page/{page}"

    card_selectors = ['article', 'div.item', 'div.result-item', 'div.flw-item', 'div.ml-item', '.boxgrid', 'li.item', '.card']
    title_selectors = ['h1', 'h2', 'h3', '.title', '.entry-title', 'a.name', 'h2.film-name', 'h3.film-name', '.card__title']
    poster_attrs = ("data-src", "data-original", "data-lazy-src", "src")

    # ---- helpers ----------------------------------------------------------
    def _get(self, url, referer=None):
        return safe_get(url, headers=get_headers(referer=referer or self.base_url,
                                                 language=self.language), timeout=20)

    def _soup(self, url, referer=None):
        r = self._get(url, referer=referer)
        return BeautifulSoup(r.text, "html.parser")

    def _poster(self, el):
        if not el:
            return None
        img = el if el.name == "img" else el.select_one("img")
        if not img:
            return None
        for a in self.poster_attrs:
            v = img.get(a)
            if v:
                return normalize_url(v, self.base_url)
        return None

    def _title(self, el):
        for sel in self.title_selectors:
            t = el.select_one(sel)
            if t and t.get_text(strip=True):
                return t.get_text(strip=True)
        a = el.select_one("a[title]")
        if a and a.get("title"):
            return a.get("title").strip()
        img = el.select_one("img[alt]")
        if img and img.get("alt"):
            return img.get("alt").strip()
        return None

    def _href(self, el):
        a = el.select_one("a[href]")
        if a:
            return normalize_url(a.get("href"), self.base_url)
        if el.name == "a" and el.get("href"):
            return normalize_url(el.get("href"), self.base_url)
        return None

    def _is_movie(self, href):
        h = (href or "").lower()
        if any(k in h for k in ("/pelicula", "/film", "/movie", "/movies")):
            return True
        if any(k in h for k in ("/serie", "/series", "/tv", "/anime", "/tvshow")):
            return False
        return None

    def _parse_cards(self, soup):
        out = []
        seen = set()
        cards = []
        for sel in self.card_selectors:
            cards = soup.select(sel)
            if cards:
                break
        for el in cards:
            href = self._href(el)
            title = self._title(el)
            if not href or not title or href in seen:
                continue
            seen.add(href)
            poster = self._poster(el)
            mv = self._is_movie(href)
            if mv is True or (mv is None and not self.supports_tv_shows):
                if self.supports_movies:
                    out.append(Movie(id=href, title=title, poster=poster))
                elif self.supports_tv_shows:
                    out.append(TvShow(id=href, title=title, poster=poster))
            else:
                if self.supports_tv_shows:
                    out.append(TvShow(id=href, title=title, poster=poster))
                elif self.supports_movies:
                    out.append(Movie(id=href, title=title, poster=poster))
        return out

    # ---- discovery --------------------------------------------------------
    def get_home(self):
        return []

    def search(self, query, page=1):
        if not query:
            return []
        try:
            url = self.search_path.format(base=self.base_url, q=query, query=query, page=page)
            return self._parse_cards(self._soup(url))
        except Exception as e:
            print(f"[AnimeOnlineNinjaProvider] search error: {e}")
            return []

    def get_movies(self, page=1):
        if not self.supports_movies:
            return []
        try:
            url = self.movies_path.format(base=self.base_url, page=page)
            return [x for x in self._parse_cards(self._soup(url))
                    if getattr(x, "type", "") == "movie"] or self._parse_cards(self._soup(url))
        except Exception as e:
            print(f"[AnimeOnlineNinjaProvider] get_movies error: {e}")
            return []

    def get_tv_shows(self, page=1):
        if not self.supports_tv_shows:
            return []
        try:
            url = self.tv_path.format(base=self.base_url, page=page)
            return [x for x in self._parse_cards(self._soup(url))
                    if getattr(x, "type", "") == "tv"] or self._parse_cards(self._soup(url))
        except Exception as e:
            print(f"[AnimeOnlineNinjaProvider] get_tv_shows error: {e}")
            return []

    # ---- details ----------------------------------------------------------
    def _details(self, item_id):
        soup = self._soup(item_id, referer=item_id)
        title = None
        for sel in ("h1", "h1.entry-title", ".data h1", ".sheader h1", "h2.title"):
            t = soup.select_one(sel)
            if t and t.get_text(strip=True):
                title = t.get_text(strip=True)
                break
        title = title or "Unknown"
        poster = None
        for sel in (".poster img", ".sheader .poster img", "img.poster",
                    ".post-thumbnail img", "div.thumb img", "article img"):
            p = soup.select_one(sel)
            if p:
                poster = self._poster(p) or normalize_url(p.get("src", ""), self.base_url)
                if poster:
                    break
        return soup, title, poster

    def get_movie(self, item_id):
        try:
            _, title, poster = self._details(item_id)
            return Movie(id=item_id, title=title, poster=poster)
        except Exception as e:
            print(f"[AnimeOnlineNinjaProvider] get_movie error: {e}")
            return Movie(id=item_id, title="Unknown")

    def get_tv_show(self, item_id):
        try:
            soup, title, poster = self._details(item_id)
            show = TvShow(id=item_id, title=title, poster=poster)
        except Exception as e:
            print(f"[AnimeOnlineNinjaProvider] get_tv_show error: {e}")
            show = TvShow(id=item_id, title="Unknown")
        seasons = []
        try:
            nums = set()
            for el in soup.select(".se-c, .season, div[data-season], li.sea, .choose-season a"):
                m = re.search(r"(\d+)", el.get("data-season") or el.get_text(" ", strip=True) or "")
                if m:
                    nums.add(int(m.group(1)))
            for n in sorted(nums):
                seasons.append(Season(id=f"{item_id}#s{n}", number=n, title=f"Stagione {n}"))
        except Exception:
            pass
        if not seasons:
            seasons = [Season(id=f"{item_id}#s1", number=1, title="Stagione 1")]
        show.seasons = seasons
        return show

    def get_episodes_by_season(self, season_id):
        show_id = season_id.split("#s")[0]
        try:
            snum = int(season_id.split("#s")[-1])
        except Exception:
            snum = 1
        eps = []
        try:
            soup = self._soup(show_id, referer=show_id)
            selectors = (".episodios li", ".se-c .episodios li", "ul.episodios li",
                         "div.episode a", "a.episode", "li.episode a", ".ep-item")
            found = []
            for sel in selectors:
                found = soup.select(sel)
                if found:
                    break
            for i, el in enumerate(found):
                a = el if el.name == "a" else el.select_one("a[href]")
                href = normalize_url(a.get("href"), self.base_url) if a else f"{season_id}#e{i+1}"
                numtxt = ""
                nm = el.select_one(".numerando")
                if nm:
                    numtxt = nm.get_text(strip=True)
                text = (a.get_text(strip=True) if a else el.get_text(strip=True)) or f"Episodio {i+1}"
                m = re.search(r"(\d+)\s*$", numtxt) or re.search(r"(\d+)", text)
                num = int(m.group(1)) if m else i + 1
                eps.append(Episode(id=href, number=num, title=text))
            if not eps:
                eps = [Episode(id=f"{season_id}#e1", number=1, title="Episodio 1")]
        except Exception as e:
            print(f"[AnimeOnlineNinjaProvider] episodes error: {e}")
            eps = [Episode(id=f"{season_id}#e1", number=1, title="Episodio 1")]
        return eps

    # ---- playback ---------------------------------------------------------
    def _collect_servers(self, item_id):
        servers = []
        seen = set()
        try:
            soup = self._soup(item_id, referer=item_id)
            for ifr in soup.select("iframe[src], iframe[data-src]"):
                src = ifr.get("src") or ifr.get("data-src")
                src = normalize_url(src, self.base_url)
                if src and src not in seen and src.startswith("http"):
                    seen.add(src)
                    servers.append(Server(id=src, name="Player", src=src))
            for a in soup.select("a[data-embed], a[data-src], a[data-url], li[data-embed]"):
                src = a.get("data-embed") or a.get("data-src") or a.get("data-url")
                src = normalize_url(src or "", self.base_url)
                label = a.get_text(strip=True) or "Server"
                if src and src not in seen and src.startswith("http"):
                    seen.add(src)
                    servers.append(Server(id=src, name=label, src=src))
            for a in soup.select("ul.dooplay_player_option li, .dooplay_player_option, .server a, .player-option"):
                src = a.get("data-option") or a.get("data-src") or ""
                src = normalize_url(src, self.base_url)
                label = a.get_text(strip=True) or "Server"
                if src and src not in seen and src.startswith("http"):
                    seen.add(src)
                    servers.append(Server(id=src, name=label, src=src))
        except Exception as e:
            print(f"[AnimeOnlineNinjaProvider] servers error: {e}")
        return servers

    def get_servers(self, item_id, video_type):
        return self._collect_servers(item_id)

    def get_video(self, server):
        try:
            src = server.src
        except Exception:
            src = ""
        return extract(src, server)
