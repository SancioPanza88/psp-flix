"""Base Provider contract for PSPflix.

Mirrors com.streamflixreborn.streamflix.proiders.Provider:
each provider exposes a `language` (auto-selected from the provider)
and the same discovery/playback methods. Movie vs TV is decided by
the item's `.type` attribute ("movie" or "tv").
"""
from ..models import Movie, TvShow, Episode, Season, Genre, People, Category, Video, Server


class Provider:
    #: Primary domain. May be rotated/overridden per provider.
    base_url = ""
    #: Display name shown in the UI.
    name = ""
    #: Logo url (optional, used by GUI).
    logo = ""
    #: ISO language code this provider serves ("it", "en", ...).
    language = "en"

    # ---- optional capability flags (mirrors ProviderSupport) ----
    supports_movies = True
    supports_tv_shows = True

    def get_home(self) -> list[Category]:
        """Return home-page categories. Default: search with empty query."""
        return []

    def search(self, query: str, page: int = 1) -> list:
        """Return list of Movie/TvShow/Genere items for `query`.
        Empty query should return browse entries (e.g. genres)."""
        raise NotImplementedError

    def get_movies(self, page: int = 1) -> list[Movie]:
        raise NotImplementedError

    def get_tv_shows(self, page: int = 1) -> list[TvShow]:
        raise NotImplementedError

    def get_movie(self, item_id: str) -> Movie:
        raise NotImplementedError

    def get_tv_show(self, item_id: str) -> TvShow:
        raise NotImplementedError

    def get_episodes_by_season(self, season_id: str) -> list[Episode]:
        raise NotImplementedError

    def get_genre(self, genre_id: str, page: int = 1) -> Genre:
        return Genre(id=genre_id, name="", shows=[])

    def get_people(self, people_id: str, page: int = 1) -> People:
        return People(id=people_id, name="")

    def get_servers(self, item_id: str, video_type: str) -> list[Server]:
        """Return list of Server for a movie/episode id.
        video_type is "movie" or "episode"."""
        raise NotImplementedError

    def get_video(self, server: Server) -> Video:
        """Resolve a Server into a playable Video (stream url)."""
        raise NotImplementedError

    # ----- helpers shared by subclasses -----
    @staticmethod
    def _is_movie_id(item_id: str) -> bool:
        return "/movie/" in item_id or item_id.endswith("/movie")

    def __repr__(self):
        return f"<{type(self).__name__} {self.name} [{self.language}]>"
