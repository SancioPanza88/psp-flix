"""Cuevana 3 provider (ported from CuevanaEuProvider.kt).

Faithful port of the Android Kotlin provider. It uses the site's
`wp-api/v1` JSON endpoints as the primary path and HTML scraping as a
fallback. Movie vs TvShow is decided exactly like the Android app:
the API `post.type` field ("movies" -> Movie, "tvshows"/"animes" ->
TvShow), or by href markers in the HTML fallback
(/pelicula/ or /peliculas/ -> Movie; /serie/, /series/, /anime/,
/animes/ -> TvShow).

Id conventions (reconstructable everywhere inside this file):
    Movie    : "peliculas/<slug>"
    TvShow   : "series/<slug>"  or  "animes/<slug>"
    Season   : "<showId>/temporada-<n>"
    Episode  : "<showId>/temporada-<n>/episodio-<m>"
"""
import re
from urllib.parse import urlparse, urljoin, parse_qs, quote

from .base import Provider
from ..models import Movie, TvShow, Episode, Season, Genre, Category, Video, Server
from ..extractors import extract
from ..extractors._shared import safe_get, get_headers, normalize_url
from bs4 import BeautifulSoup


DEFAULT_CUEVANA_DOMAIN = "cuevana.gs"


class CuevanaEuProvider(Provider):
    base_url = f"https://{DEFAULT_CUEVANA_DOMAIN}"
    name = "Cuevana 3"
    language = "es"
    supports_movies = True
    supports_tv_shows = True

    logo = f"https://{DEFAULT_CUEVANA_DOMAIN}/wp-content/uploads/2026/03/cropped-unnamed-removebg-preview-32x32.png"

    # ---- low level helpers ------------------------------------------------
    def _api_get(self, path, params=None):
        """Call wp-api/v1/<path> and return the decoded `data` field,
        or None on any failure."""
        try:
            url = f"{self.base_url}/wp-api/v1/{path.lstrip('/')}"
            r = safe_get(url,
                         headers=get_headers(referer=self.base_url, language=self.language,
                                             extra={"Accept": "application/json, text/plain, */*"}),
                         timeout=20, params=params)
            if not r or r.status_code >= 400:
                return None
            body = r.json()
            if isinstance(body, dict) and "data" in body:
                return body.get("data")
            return body
        except Exception as e:
            print(f"[Cuevana] api error ({path}): {e}")
            return None

    def _page(self, url, referer=None):
        """Fetch an HTML page and return a BeautifulSoup object."""
        r = safe_get(url, headers=get_headers(referer=referer or self.base_url,
                                              language=self.language), timeout=20)
        if not r:
            return None
        return BeautifulSoup(r.text, "html.parser")

    def _fix_image(self, url):
        if not url:
            return None
        url = url.strip()
        if url.startswith("data:image"):
            return None
        if url.startswith("http"):
            return url
        if url.startswith("//"):
            return "https:" + url
        return f"{self.base_url}/{url.lstrip('/')}"

    @staticmethod
    def _extract_slug(item_id):
        return item_id.strip("/").split("/")[-1]

    @staticmethod
    def _is_movie_href(href):
        return "/pelicula/" in href or "/peliculas/" in href

    @staticmethod
    def _is_tv_href(href):
        return ("/serie/" in href or "/series/" in href or
                "/anime/" in href or "/animes/" in href)

    @staticmethod
    def _show_post_type(item_id):
        first = item_id.strip("/").split("/")[0]
        return "animes" if first in ("anime", "animes") else "tvshows"

    @staticmethod
    def _parse_runtime(value):
        m = re.search(r"\d+", value or "")
        return int(m.group()) if m else None

    @staticmethod
    def _parse_rating(post):
        for k in ("community_rating", "rating"):
            v = post.get(k) if isinstance(post, dict) else None
            if v:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return None

    @staticmethod
    def _parse_year(value):
        m = re.search(r"(?:19|20)\d{2}", value or "")
        return int(m.group()) if m else None

    def _norm_trailer(self, value):
        if not value:
            return None
        if value.startswith("http"):
            return value
        if value.startswith("//"):
            return "https:" + value
        if len(value) == 11 and "/" not in value:
            return f"https://www.youtube.com/watch?v={value}"
        return value

    # ---- mapping ----------------------------------------------------------
    def _map_post(self, post):
        """Map a FastApiSinglePost dict to Movie or TvShow."""
        try:
            ptype = (post.get("type") or "").lower()
            slug = post.get("slug")
            if not slug:
                return None
            poster = self._fix_image(post.get("poster") or post.get("images", {}).get("poster"))
            banner = self._fix_image(post.get("backdrop") or post.get("images", {}).get("backdrop"))
            common = dict(
                title=post.get("title", "Unknown"),
                overview=post.get("overview"),
                released=post.get("release_date"),
                rating=self._parse_rating(post),
                poster=poster,
                banner=banner,
            )
            if ptype == "movies":
                return Movie(id=f"peliculas/{slug}", **common)
            if ptype in ("tvshows", "animes"):
                prefix = "animes" if ptype == "animes" else "series"
                return TvShow(id=f"{prefix}/{slug}", **common)
        except Exception as e:
            print(f"[Cuevana] map_post error: {e}")
        return None

    def _build_server_name(self, entry):
        url = entry.get("url", "")
        server = None
        try:
            qs = parse_qs(urlparse(url).query)
            server = qs.get("server", [None])[0]
        except Exception:
            pass
        if not server:
            host = urlparse(url).netloc
            if host:
                server = host[4:] if host.startswith("www.") else host
        if not server:
            server = entry.get("server")
        if not server:
            server = "Server"
        parts = [server]
        if entry.get("lang"):
            parts.append(entry["lang"])
        if entry.get("quality"):
            parts.append(entry["quality"])
        return " | ".join(parts)

    # ---- discovery --------------------------------------------------------
    def get_home(self):
        try:
            cats = []
            sections = [
                ("/listing/movies", "postType=movies", "Últimas Películas"),
                ("/listing/tvshows", "postType=tvshows", "Últimas Series"),
                ("/listing/animes", "postType=animes", "Últimos Animes"),
            ]
            for path, _, name in sections:
                data = self._api_get(path, params={
                    "page": "1", "orderBy": "latest", "order": "desc",
                    "postType": path.split("/")[-1], "postsPerPage": "18",
                })
                posts = (data or {}).get("posts", []) if isinstance(data, dict) else []
                items = [self._map_post(p) for p in posts]
                items = [x for x in items if x]
                if items:
                    cats.append(Category(name=name, items=items))
            if cats:
                return cats
        except Exception as e:
            print(f"[Cuevana] home error: {e}")
        return []

    def search(self, query, page=1):
        if not query:
            return []
        try:
            data = self._api_get("/search", params={
                "q": query, "page": str(page),
                "postType": "any", "postsPerPage": "24",
            })
            posts = (data or {}).get("posts", []) if isinstance(data, dict) else []
            items = [self._map_post(p) for p in posts]
            items = [x for x in items if x]
            if items:
                return items
        except Exception as e:
            print(f"[Cuevana] search api error: {e}")

        try:
            soup = self._page(f"{self.base_url}/?s={quote(query)}")
            return self._parse_articles(soup)
        except Exception as e:
            print(f"[Cuevana] search fallback error: {e}")
            return []

    def get_movies(self, page=1):
        try:
            data = self._api_get("/listing/movies", params={
                "page": str(page), "orderBy": "latest", "order": "desc",
                "postType": "movies", "postsPerPage": "24",
            })
            posts = (data or {}).get("posts", []) if isinstance(data, dict) else []
            items = [m for m in (self._map_post(p) for p in posts) if m and m.type == "movie"]
            if items:
                return items
        except Exception as e:
            print(f"[Cuevana] get_movies api error: {e}")

        try:
            urls = [
                f"{self.base_url}/peliculas/" if page == 1 else f"{self.base_url}/peliculas/page/{page}/",
                f"{self.base_url}/pelicula/" if page == 1 else f"{self.base_url}/pelicula/page/{page}/",
            ]
            for url in urls:
                soup = self._page(url)
                movies = self._parse_articles(soup, movies_only=True)
                if movies:
                    return movies
        except Exception as e:
            print(f"[Cuevana] get_movies fallback error: {e}")
        return []

    def get_tv_shows(self, page=1):
        items = []
        try:
            for pt in ("tvshows", "animes"):
                data = self._api_get(f"/listing/{pt}", params={
                    "page": str(page), "orderBy": "latest", "order": "desc",
                    "postType": pt, "postsPerPage": "24",
                })
                posts = (data or {}).get("posts", []) if isinstance(data, dict) else []
                items += [t for t in (self._map_post(p) for p in posts) if t and t.type == "tv"]
        except Exception as e:
            print(f"[Cuevana] get_tv_shows api error: {e}")

        if items:
            seen = set()
            out = []
            for it in items:
                if it.id not in seen:
                    seen.add(it.id)
                    out.append(it)
            return out

        try:
            urls = [
                f"{self.base_url}/series/" if page == 1 else f"{self.base_url}/series/page/{page}/",
                f"{self.base_url}/serie/" if page == 1 else f"{self.base_url}/serie/page/{page}/",
            ]
            for url in urls:
                soup = self._page(url)
                shows = self._parse_articles(soup, tv_only=True)
                if shows:
                    return shows
        except Exception as e:
            print(f"[Cuevana] get_tv_shows fallback error: {e}")
        return []

    def _parse_articles(self, soup, movies_only=False, tv_only=False):
        out = []
        if not soup:
            return out
        for article in soup.select("article.tooltip-content, article"):
            anchor = article.select_one("h2 a, a[href]")
            if not anchor:
                continue
            href = anchor.get("attr", "") or anchor.get("href", "")
            if not href:
                continue
            if movies_only and not self._is_movie_href(href):
                continue
            if tv_only and not self._is_tv_href(href):
                continue
            title = anchor.get_text(strip=True)
            if not title:
                h = article.select_one("h2, h3")
                title = h.get_text(strip=True) if h else ""
            if not title:
                img = article.select_one("img[alt]")
                title = img.get("alt", "") if img else ""
            title = title.strip()
            poster = None
            img = article.select_first("img") if hasattr(article, "select_first") else article.select_one("img")
            if img:
                poster = self._fix_image(img.get("data-src") or img.get("src") or
                                         img.get("data-lazy-src") or
                                         img.get("srcset", "").split(" ")[0])
            item_id = href.split(self.base_url, 1)[-1].lstrip("/")
            if self._is_movie_href(href):
                out.append(Movie(id=item_id, title=title, poster=poster))
            elif self._is_tv_href(href):
                out.append(TvShow(id=item_id, title=title, poster=poster))
        return out

    # ---- details ----------------------------------------------------------
    def get_movie(self, item_id):
        slug = self._extract_slug(item_id)
        try:
            post = self._api_get("/single/movies", params={"slug": slug, "postType": "movies"})
            if post:
                mapped = self._map_post(post)
                if mapped:
                    return mapped
        except Exception as e:
            print(f"[Cuevana] get_movie api error: {e}")

        try:
            soup = self._page(f"{self.base_url}/{item_id}/")
            title = soup.select_one("h1")
            title = title.get_text(strip=True) if title else "Unknown"
            poster = None
            fig = soup.select_one("div.self-start figure img, div.Image img")
            if fig:
                poster = self._fix_image(fig.get("data-src") or fig.get("src"))
            overview = soup.select_one("div.entry p")
            overview = overview.get_text(strip=True) if overview else None
            return Movie(id=item_id, title=title, poster=poster, overview=overview)
        except Exception as e:
            print(f"[Cuevana] get_movie fallback error: {e}")
            return Movie(id=item_id, title="Unknown")

    def get_tv_show(self, item_id):
        slug = self._extract_slug(item_id)
        post_type = self._show_post_type(item_id)
        try:
            post = self._api_get(f"/single/{post_type}", params={"slug": slug, "postType": post_type})
            if post:
                show = self._map_post(post) or TvShow(id=item_id, title="Unknown")
                show.seasons = self._get_cuevana_seasons(item_id, post)
                return show
        except Exception as e:
            print(f"[Cuevana] get_tv_show api error: {e}")

        try:
            soup = self._page(f"{self.base_url}/{item_id}/")
            title = soup.select_one("h1")
            title = title.get_text(strip=True) if title else "Unknown"
            poster = None
            fig = soup.select_one("div.self-start figure img, div.Image img")
            if fig:
                poster = self._fix_image(fig.get("data-src") or fig.get("src"))
            seasons = self._parse_seasons_html(soup, item_id)
            show = TvShow(id=item_id, title=title, poster=poster, seasons=seasons)
            return show
        except Exception as e:
            print(f"[Cuevana] get_tv_show fallback error: {e}")
            show = TvShow(id=item_id, title="Unknown")
            show.seasons = []
            return show

    def _get_cuevana_seasons(self, show_id, fast_post):
        try:
            data = self._api_get("/single/episodes/list", params={
                "_id": str(fast_post.get("_id")), "season": "1",
                "page": "1", "postsPerPage": "1",
            })
            if not data:
                return []
            season_numbers = []
            seasons_raw = data.get("seasons", []) if isinstance(data, dict) else []
            for s in seasons_raw:
                m = re.search(r"\d+", str(s))
                if m:
                    season_numbers.append(int(m.group()))
            if not season_numbers:
                posts = data.get("posts", []) if isinstance(data, dict) else []
                season_numbers = sorted({p.get("season_number") for p in posts if p.get("season_number")})
            return [Season(id=f"{show_id}/temporada-{n}", number=n, title=f"Temporada {n}")
                    for n in season_numbers]
        except Exception as e:
            print(f"[Cuevana] seasons api error: {e}")
            return []

    @staticmethod
    def _parse_seasons_html(soup, show_id):
        out = []
        if not soup:
            return out
        for el in soup.select("div.se-q, button[data-season], .se-nav li span, .se-c, #seasons .se-q"):
            num = None
            t = el.select_one(".se-t")
            if t:
                m = re.search(r"\d+", t.get_text())
                if m:
                    num = int(m.group())
            if num is None:
                ds = el.get("data-season")
                if ds and ds.isdigit():
                    num = int(ds)
            if num is None:
                m = re.search(r"\d+", el.get_text())
                if m:
                    num = int(m.group())
            if num:
                out.append(Season(id=f"{show_id}/temporada-{num}", number=num, title=f"Temporada {num}"))
        seen = set()
        res = []
        for s in out:
            if s.number not in seen:
                seen.add(s.number)
                res.append(s)
        return sorted(res, key=lambda s: s.number)

    # ---- episodes ---------------------------------------------------------
    def get_episodes_by_season(self, season_id):
        try:
            season_number = int(season_id.rstrip("/").split("-")[-1])
        except (ValueError, IndexError):
            season_number = 1
        series_slug = season_id.split("/temporada")[0] if "/temporada" in season_id else season_id
        slug = self._extract_slug(series_slug)
        post_type = self._show_post_type(series_slug)

        try:
            post = self._api_get(f"/single/{post_type}", params={"slug": slug, "postType": post_type})
            if post and post.get("_id"):
                data = self._api_get("/single/episodes/list", params={
                    "_id": str(post.get("_id")), "season": str(season_number),
                    "page": "1", "postsPerPage": "100",
                })
                if data:
                    posts = data.get("posts", []) if isinstance(data, dict) else []
                    if posts:
                        eps = []
                        for p in sorted(posts, key=lambda x: x.get("episode_number", 0)):
                            ep_id = f"{series_slug}/temporada-{season_number}/episodio-{p.get('episode_number')}"
                            eps.append(Episode(
                                id=ep_id,
                                number=p.get("episode_number", 0),
                                title=p.get("title"),
                                poster=self._fix_image(p.get("still_path")),
                                overview=p.get("overview"),
                                released=p.get("date"),
                            ))
                        return eps
        except Exception as e:
            print(f"[Cuevana] episodes api error: {e}")

        # Fallback: fetch the series HTML page, grab post_id + nonce.
        try:
            url = f"{self.base_url}/{series_slug.strip('/')}/"
            soup = self._page(url)
            if soup:
                post_id = None
                pw = soup.select_one("#season-wrapper")
                if pw:
                    post_id = pw.get("data-post-id")
                if not post_id:
                    pw = soup.select_one("#player-wrapper")
                    if pw:
                        post_id = pw.get("data-post_id")
                nonce = ""
                m = re.search(r'window\.wpApiSettings\s*=\s*\{[^}]*"nonce":"([^"]+)"', soup.decode(), re.DOTALL)
                if m:
                    nonce = m.group(1)
                if post_id and nonce:
                    api_url = (f"{self.base_url}/wp-json/cuevana/v1/get-season-episodes"
                               f"?id={post_id}&season={season_number}")
                    r = safe_get(api_url, headers=get_headers(
                        referer=url, language=self.language,
                        extra={"X-WP-Nonce": nonce, "Accept": "application/json, */*"}), timeout=20)
                    if r and r.status_code < 400:
                        try:
                            j = r.json()
                            eps = []
                            for c in j.get("episodes", []):
                                enum = c.get("enum") or c.get("episode_number") or 0
                                snum = c.get("snum") or season_number
                                ep_id = f"{series_slug}/temporada-{snum}/episodio-{enum}"
                                eps.append(Episode(
                                    id=ep_id,
                                    number=enum,
                                    title=c.get("name"),
                                    poster=(f"https://image.tmdb.org/t/p/w300{c['still_path']}"
                                            if c.get("still_path") else None),
                                ))
                            if eps:
                                return eps
                        except Exception as e:
                            print(f"[Cuevana] episodes json error: {e}")
                # last resort HTML parse
                return self._fallback_episodes(soup, season_id, season_number, series_slug)
        except Exception as e:
            print(f"[Cuevana] episodes fallback error: {e}")
        return []

    def _fallback_episodes(self, soup, season_id, season_number, series_slug):
        out = []
        if not soup:
            return out
        for el in soup.select("ul.episodios li, .episodios li, article.episodio, #season-episodes article"):
            a = el.select_one("h2 a, .episodiotitle a, a")
            if not a:
                continue
            href = a.get("href", "")
            if not href or ("/episodio/" not in href and "-temporada-" not in href):
                continue
            ep_title = a.get_text(strip=True)
            num_txt = ""
            n = el.select_one("span.bg-main, .numerando, .ep-num")
            if n:
                num_txt = n.get_text()
            m = re.search(r"\d+", num_txt)
            ep_num = int(m.group()) if m else 0
            poster = None
            img = el.select_one("img.poster, .episodioimage img, img")
            if img:
                poster = self._fix_image(img.get("data-src") or img.get("src"))
            ep_id = href.split(self.base_url, 1)[-1].lstrip("/")
            out.append(Episode(id=ep_id, number=ep_num, title=ep_title, poster=poster))
        return out

    # ---- playback ---------------------------------------------------------
    def get_servers(self, item_id, video_type):
        try:
            post_id = None
            if video_type == "movie":
                post = self._api_get("/single/movies", params={
                    "slug": self._extract_slug(item_id), "postType": "movies"})
                if post:
                    post_id = post.get("_id")
            else:  # episode
                ep = self._api_get("/single/episodes", params={
                    "slug": self._extract_slug(item_id), "postType": "episodes"})
                if ep and ep.get("episode"):
                    post_id = ep["episode"].get("_id")
                if not post_id:
                    # derive from the show + season/episode numbers in id
                    parts = item_id.strip("/").split("/")
                    show_slug = self._extract_slug(parts[0])
                    post_type = self._show_post_type(parts[0])
                    show = self._api_get(f"/single/{post_type}", params={"slug": show_slug, "postType": post_type})
                    if show and show.get("_id"):
                        m = re.search(r"temporada-(\d+)", item_id)
                        s_num = m.group(1) if m else "1"
                        m2 = re.search(r"episodio-(\d+)", item_id)
                        e_num = m2.group(1) if m2 else None
                        data = self._api_get("/single/episodes/list", params={
                            "_id": str(show.get("_id")), "season": s_num,
                            "page": "1", "postsPerPage": "100"})
                        if data:
                            posts = data.get("posts", []) if isinstance(data, dict) else []
                            for p in posts:
                                if (str(p.get("season_number")) == str(s_num) and
                                        (e_num is None or str(p.get("episode_number")) == str(e_num))):
                                    post_id = p.get("_id")
                                    break
            if not post_id:
                return []

            data = self._api_get("/player", params={"postId": str(post_id), "demo": "0"})
            if not data:
                return []
            embeds = data.get("embeds", []) if isinstance(data, dict) else []
            servers = []
            for entry in embeds:
                url = entry.get("url", "")
                if not url:
                    continue
                servers.append(Server(id=url, name=self._build_server_name(entry), src=""))
            # de-dup by url
            seen = set()
            out = []
            for s in servers:
                if s.id not in seen:
                    seen.add(s.id)
                    out.append(s)
            return out
        except Exception as e:
            print(f"[Cuevana] servers error: {e}")
            return []

    def get_video(self, server):
        try:
            final_url = server.id or ""
            if "/player.php?" in final_url:
                r = safe_get(final_url, headers=get_headers(referer=self.base_url, language=self.language), timeout=20)
                if r:
                    m = re.search(r'<iframe[^>]+src="([^"]+)"', r.text, re.IGNORECASE)
                    if m:
                        final_url = m.group(1)
            if "app.mysync.mov/stream/" in final_url:
                r = safe_get(final_url, headers=get_headers(referer=self.base_url, language=self.language), timeout=20)
                if r:
                    m = re.search(r'window\.location\.replace\("([^"]+)"', r.text)
                    if m:
                        final_url = m.group(1)
            return extract(final_url, server)
        except Exception as e:
            print(f"[Cuevana] video error: {e}")
            return Video(source="")
