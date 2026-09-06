"""Data models for PSPflix (ported from StreamFlix Android models).

Only the fields needed by the PSP downloader are kept:
identity, title, poster/banner art, and the season/episode
hierarchy used to pick what to download.
"""


class Show:
    def __init__(self, id, title, poster=None, banner=None,
                 overview=None, released=None, rating=None, quality=None,
                 is_favorite=False):
        self.id = id
        self.title = title
        self.poster = poster
        self.banner = banner
        self.overview = overview
        self.released = released
        self.rating = rating
        self.quality = quality
        self.is_favorite = is_favorite


class Movie(Show):
    def __init__(self, id, title, **kwargs):
        super().__init__(id, title, **kwargs)
        self.type = "movie"


class TvShow(Show):
    def __init__(self, id, title, seasons=None, **kwargs):
        super().__init__(id, title, **kwargs)
        self.type = "tv"
        self.seasons = seasons or []


class Season:
    def __init__(self, id, number, title=None, poster=None, episodes=None):
        self.id = id
        self.number = number
        self.title = title
        self.poster = poster
        self.episodes = episodes or []


class Episode:
    def __init__(self, id, number, title=None, poster=None, overview=None):
        self.id = id
        self.number = number
        self.title = title
        self.poster = poster
        self.overview = overview


class Genre:
    def __init__(self, id, name, shows=None):
        self.id = id
        self.name = name
        self.shows = shows or []


class People:
    def __init__(self, id, name, image=None, filmography=None):
        self.id = id
        self.name = name
        self.image = image
        self.filmography = filmography or []


class Category:
    FEATURED = "Featured"

    def __init__(self, name, items):
        self.name = name
        self.list = items


class Video:
    """Resolved playable video, mirrors StreamFlix's Video model."""

    def __init__(self, source, subtitles=None, headers=None,
                 server_name=None, referer=None, extra_buffering=False):
        self.source = source
        self.subtitles = subtitles or []
        self.headers = headers or {}
        self.server_name = server_name
        self.referer = referer
        self.extra_buffering = extra_buffering


class Server:
    """A playable server option for a movie/episode (mirrors Video.Server)."""

    def __init__(self, id, name, src=""):
        self.id = id
        self.name = name
        self.src = src
