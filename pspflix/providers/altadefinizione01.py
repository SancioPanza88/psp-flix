"""Altadefinizione01 provider (ported from Altadefinizione01Provider.kt).

Faithful port of the parts PSPflix needs: search, movie/tv listing with
correct type detection, seasons/episodes and server resolution.
Network/parse failures return empty results instead of raising.
"""
import re
from urllib.parse import quote, urlparse
from .base import Provider
from ..models import Movie, TvShow, Episode, Season, Genre, Category, Video, Server
from ..extractors import extract
from ..extractors._shared import safe_get, get_headers, normalize_url
from bs4 import BeautifulSoup


class Altadefinizione01Provider(Provider):
    base_url = "https://altadefinizione-01.fun"
    name = "Altadefinizione01"
    language = "it"
    supports_movies = True
    supports_tv_shows = True

    # ---- helpers ----------------------------------------------------------
    def _get(self, url, referer=None):
        return safe_get(url, headers=get_headers(referer=referer or self.base_url,
                                                 language=self.language), timeout=25)

    def _soup(self, url, referer=None):
        r = self._get(url, referer=referer)
        return BeautifulSoup(r.text, "html.parser")

    def _norm(self, url):
        return normalize_url(url or "", self.base_url)

    def _parse_grid_item(self, el):
        """Faithful port of parseGridItem: returns Movie or TvShow."""
        anchor = (el.select_one(".cover.boxcaption h2 a")
                  or el.select_one("h3 a")
                  or el.select_one(".boxcaption h2 a"))
        if not anchor:
            return None
        title = anchor.get_text(strip=True)
        href = self._norm(anchor.get("href", "").strip())
        if not title or not href:
            return None
        img = el.select_one("a > img") or el.select_one("img")
        poster = self._norm(img.get("data-src") or img.get("src")) if img else None

        is_tv = bool(el.select_one(".se_num")
                     or el.select_one(".ml-cat a[href*='/serie-tv/']"))
        if is_tv:
            return TvShow(id=href, title=title, poster=poster)
        return Movie(id=href, title=title, poster=poster)

    def _parse_cards(self, soup):
        out = []
        for el in soup.select("#dle-content .boxgrid.caption, .boxgrid.caption"):
            item = self._parse_grid_item(el)
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
            encoded = quote(query, safe="")
            url = (f"{self.base_url}/index.php?do=search&subaction=search"
                   f"&titleonly=3&story={encoded}&full_search=0")
            if page > 1:
                result_from = (page - 1) * 50 + 1
                url += f"&search_start={page}&result_from={result_from}"
            return self._parse_cards(self._soup(url))
        except Exception as e:
            print(f"[Altadefinizione01] search error: {e}")
            return []

    def get_movies(self, page=1):
        try:
            url = (f"{self.base_url}/cinema/" if page <= 1
                   else f"{self.base_url}/cinema/page/{page}/")
            return [x for x in self._parse_cards(self._soup(url))
                    if getattr(x, "type", "") == "movie"]
        except Exception as e:
            print(f"[Altadefinizione01] get_movies error: {e}")
            return []

    def get_tv_shows(self, page=1):
        try:
            url = (f"{self.base_url}/serie-tv/" if page <= 1
                   else f"{self.base_url}/serie-tv/page/{page}/")
            return [x for x in self._parse_cards(self._soup(url))
                    if getattr(x, "type", "") == "tv"]
        except Exception as e:
            print(f"[Altadefinizione01] get_tv_shows error: {e}")
            return []

    # ---- details ----------------------------------------------------------
    def _page_title(self, soup):
        for sel in ("#single .data h1", "meta[property=og:title]", "h1", "h2", "title"):
            t = soup.select_one(sel)
            if not t:
                continue
            val = t.get("content") if t.name == "meta" else t.get_text(strip=True)
            if val:
                return (val.replace("Streaming HD - Altadefinizione01", "")
                        .replace("Streaming Gratis - Serie TV - Altadefinizione01", "")
                        .replace(" - Serie TV", "").strip())
        return "Sconosciuto"

    def _page_poster(self, soup):
        img = soup.select_one(".fix img")
        return self._norm(img.get("data-src") or img.get("src")) if img else None

    def get_movie(self, item_id):
        try:
            soup = self._soup(item_id, referer=item_id)
            return Movie(id=item_id, title=self._page_title(soup),
                         poster=self._page_poster(soup))
        except Exception as e:
            print(f"[Altadefinizione01] get_movie error: {e}")
            return Movie(id=item_id, title="Sconosciuto")

    def get_tv_show(self, item_id):
        try:
            soup = self._soup(item_id, referer=item_id)
            show = TvShow(id=item_id, title=self._page_title(soup),
                          poster=self._page_poster(soup))
        except Exception as e:
            print(f"[Altadefinizione01] get_tv_show error: {e}")
            show = TvShow(id=item_id, title="Sconosciuto")
            show.seasons = [Season(id=f"{item_id}#season-1", number=1, title="Stagione 1")]
            return show

        seasons = []
        for a in soup.select("#tt_holder .tt_season ul li a[data-toggle=tab]"):
            try:
                snum = int(a.get_text(strip=True))
            except ValueError:
                snum = 0
            eps = self._episodes_from_pane(soup, item_id, snum)
            seasons.append(Season(id=f"{item_id}#season-{snum}", number=snum,
                                  title=f"Stagione {snum}", episodes=eps))

        # --- VidxGo fallback: no HTML season tabs (mirror Kotlin getTvShow) ---
        if not seasons and soup.select_one("iframe#vidxgo-player"):
            imdb = self._find_imdb(soup)
            if imdb:
                seasons = self._vidxgo_seasons(imdb, item_id, referer=item_id)

        if not seasons:
            seasons = [Season(id=f"{item_id}#season-1", number=1, title="Stagione 1")]
        seasons.sort(key=lambda s: s.number)
        show.seasons = seasons
        return show

    # ---- VidxGo season/episode fallback (mirror Kotlin) ------------------
    def _vidxgo_seasons(self, imdb, show_id, referer):
        """Read the VidxGo player page. If it has .ep-season-tab entries,
        create empty seasons (episodes loaded later in get_episodes_by_season).
        Otherwise parse #episodesList directly. Mirrors Kotlin getTvShow."""
        parsed = urlparse(show_id)
        ref = f"{parsed.scheme}://{parsed.netloc}/"
        vidxgo_url = f"https://v.vidxgo.co/{imdb}"
        try:
            r = safe_get(vidxgo_url, headers=get_headers(referer=ref,
                         extra={"sec-fetch-dest": "iframe"}), timeout=20)
        except Exception as e:
            print(f"[Altadefinizione01] VidxGo load failed: {e}")
            return []
        if r.status_code != 200:
            return []
        vsoup = BeautifulSoup(r.text, "html.parser")

        season_tabs = vsoup.select(".ep-season-tab")
        if season_tabs:
            seasons = []
            for tab in season_tabs:
                try:
                    snum = int(tab.get("data-season", ""))
                except (ValueError, TypeError):
                    continue
                seasons.append(Season(id=f"{show_id}#season-{snum}", number=snum,
                                      title=f"Stagione {snum}", episodes=[]))
            seasons.sort(key=lambda s: s.number)
            return seasons

        # No season tabs -> parse #episodesList and group by season
        seasons_map = {}
        for ep in vsoup.select("#episodesList a.ep-item"):
            parts = ep.get("href", "").strip("/").split("/")
            if len(parts) < 3:
                continue
            try:
                snum, enum = int(parts[1]), int(parts[2])
            except ValueError:
                continue
            name_el = ep.select_one(".ep-name")
            plot_el = ep.select_one(".ep-plot")
            title = name_el.get_text(strip=True) if name_el else None
            overview = plot_el.get_text(strip=True) if plot_el else None
            seasons_map.setdefault(snum, []).append(Episode(
                id=f"{show_id}#s{snum}e{enum}",
                number=enum, title=title, overview=overview,
            ))
        seasons = []
        for snum, eps in sorted(seasons_map.items()):
            eps.sort(key=lambda x: x.number)
            seasons.append(Season(id=f"{show_id}#season-{snum}", number=snum,
                                  title=f"Stagione {snum}", episodes=eps))
        return seasons

    def _vidxgo_episodes_for_season(self, imdb, show_url, season_num):
        """Mirror Kotlin getEpisodesBySeason VidxGo branch: try seasons.php
        JSON API first, then fall back to scraping #episodesList."""
        parsed = urlparse(show_url)
        ref = f"{parsed.scheme}://{parsed.netloc}/"
        api_url = f"https://v.vidxgo.co/seasons.php?imdb={imdb}&season={season_num}"
        try:
            ra = safe_get(api_url, headers=get_headers(referer=ref,
                          extra={"sec-fetch-dest": "empty"}), timeout=20)
            if ra.status_code == 200:
                data = ra.json()
                if data.get("ok") == 1:
                    eps = []
                    for ep in data.get("episodes", []):
                        enum = ep.get("number")
                        if enum is None:
                            continue
                        eps.append(Episode(
                            id=f"{show_url}#s{season_num}e{enum}",
                            number=enum,
                            title=ep.get("name") or None,
                            overview=ep.get("overview") or None,
                        ))
                    eps.sort(key=lambda x: x.number)
                    if eps:
                        return eps
        except Exception as e:
            print(f"[Altadefinizione01] VidxGo season {season_num} API error: {e}")

        # Fallback: scrape the player page
        vidxgo_url = f"https://v.vidxgo.co/{imdb}"
        try:
            r = safe_get(vidxgo_url, headers=get_headers(referer=ref,
                         extra={"sec-fetch-dest": "iframe"}), timeout=20)
            if r.status_code != 200:
                return []
            vsoup = BeautifulSoup(r.text, "html.parser")
            eps = []
            for a in vsoup.select("#episodesList a.ep-item"):
                parts = a.get("href", "").strip("/").split("/")
                if len(parts) < 3:
                    continue
                try:
                    s, e = int(parts[1]), int(parts[2])
                except ValueError:
                    continue
                if s != season_num:
                    continue
                name_el = a.select_one(".ep-name")
                plot_el = a.select_one(".ep-plot")
                eps.append(Episode(
                    id=f"{show_url}#s{s}e{e}", number=e,
                    title=name_el.get_text(strip=True) if name_el else None,
                    overview=plot_el.get_text(strip=True) if plot_el else None,
                ))
            eps.sort(key=lambda x: x.number)
            return eps
        except Exception as e:
            print(f"[Altadefinizione01] VidxGo season {season_num} scrape error: {e}")
            return []

    def _episodes_from_pane(self, soup, show_id, season_num):
        pane = soup.select_one(f"#season-{season_num}")
        eps = []
        if not pane:
            return eps
        for ep in pane.select("ul > li > a[allowfullscreen][data-link]"):
            data_num = ep.get("data-num", "")
            num = None
            if "x" in data_num:
                try:
                    num = int(data_num.split("x")[-1])
                except ValueError:
                    num = None
            if num is None:
                try:
                    num = int(ep.get_text(strip=True))
                except ValueError:
                    num = len(eps) + 1
            raw_title = ep.get("data-title", "").strip()
            if ":" in raw_title:
                ep_title = raw_title.split(":")[0].strip() or None
                overview = raw_title.split(":", 1)[1].strip() or None
            else:
                ep_title = raw_title or None
                overview = None
            eps.append(Episode(id=f"{show_id}#s{season_num}e{num}", number=num,
                               title=ep_title or f"Episodio {num}", overview=overview))
        return eps

    def get_episodes_by_season(self, season_id):
        show_url = season_id.split("#")[0]
        try:
            season_num = int(season_id.split("#season-")[-1])
        except (ValueError, IndexError):
            season_num = 1
        try:
            soup = self._soup(show_url, referer=show_url)
            eps = self._episodes_from_pane(soup, show_url, season_num)
            if eps:
                return eps
            # VidxGo fallback (mirror Kotlin getEpisodesBySeason)
            if soup.select_one("iframe#vidxgo-player"):
                imdb = self._find_imdb(soup)
                if imdb:
                    return self._vidxgo_episodes_for_season(imdb, show_url, season_num)
        except Exception as e:
            print(f"[Altadefinizione01] episodes error: {e}")
        return []

    # ---- playback ---------------------------------------------------------
    def get_servers(self, item_id, video_type):
        is_ep = video_type == "episode" or re.search(r"#s\d+e\d+", item_id)
        try:
            if is_ep:
                return self._episode_servers(item_id)
            return self._movie_servers(item_id)
        except Exception as e:
            print(f"[Altadefinizione01] servers error: {e}")
            return []

    def _episode_servers(self, item_id):
        show_url = item_id.split("#")[0]
        m = re.search(r"#s(\d+)e(\d+)", item_id)
        season_num = int(m.group(1)) if m else 0
        ep_num = int(m.group(2)) if m else 0
        soup = self._soup(show_url, referer=show_url)
        results = []
        pane = soup.select_one(f"#season-{season_num}")
        ep_anchor = None
        if pane:
            for a in pane.select("ul > li > a[allowfullscreen][data-link]"):
                data_num = a.get("data-num", "")
                n = None
                if "x" in data_num:
                    try:
                        n = int(data_num.split("x")[-1])
                    except ValueError:
                        n = None
                if n is None:
                    try:
                        n = int(a.get_text(strip=True))
                    except ValueError:
                        n = -1
                if n == ep_num:
                    ep_anchor = a
                    break
        if ep_anchor is not None and ep_anchor.parent:
            for mrr in ep_anchor.parent.select(".mirrors a[data-link]"):
                if "4K" in mrr.get_text():
                    continue
                link = mrr.get("data-link", "").strip()
                if not link:
                    continue
                link = self._norm(link)
                name = mrr.get_text(strip=True) or "Server"
                results.append(Server(id=link, name=name, src=link))
        self._append_vidxgo_episode(soup, results, season_num, ep_num)
        return results

    def _append_vidxgo_episode(self, soup, results, season_num, ep_num):
        if not soup.select_one("iframe#vidxgo-player"):
            return
        imdb = self._find_imdb(soup)
        if imdb:
            url = f"https://v.vidxgo.co/t/{imdb}/{season_num}/{ep_num}"
            results.append(Server(id=url, name="VidxGo", src=url))

    def _movie_servers(self, item_id):
        soup = self._soup(item_id, referer=item_id)
        results = []
        guardahd = soup.select_one("iframe[src*='guardahd.stream']")
        if guardahd:
            embed_url = self._norm(guardahd.get("src", ""))
            try:
                edoc = self._soup(embed_url, referer=item_id)
                for li in edoc.select("ul._player-mirrors li[data-link]"):
                    cls = li.get("class") or []
                    if "fullhd" in cls or "4K" in li.get_text():
                        continue
                    link = li.get("data-link", "").strip()
                    if not link:
                        continue
                    link = self._norm(link)
                    name = (li.get_text(strip=True) or "Server")
                    results.append(Server(id=link, name=name, src=link))
                if results:
                    return results
            except Exception as e:
                print(f"[Altadefinizione01] guardahd error: {e}")
        if (soup.select_one("iframe#vidxgo-player-film")
                or soup.select_one("iframe#vidxgo-player")):
            imdb = self._find_imdb(soup)
            if imdb:
                url = f"https://v.vidxgo.co/{imdb}"
                results.append(Server(id=url, name="VidxGo", src=url))
        return results

    def _find_imdb(self, soup):
        for script in soup.select("script"):
            m = re.search(r"var\s+imdb\s*=\s*['\"]tt(\d+)['\"]", script.get_text() or "")
            if m:
                return m.group(1)
        return None

    def get_video(self, server):
        try:
            src = server.src
        except Exception:
            src = ""
        return extract(src, server)
