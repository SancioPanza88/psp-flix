"""PSPflix — multi-provider streaming downloader for PlayStation Portable.

A Python port of the Android StreamFlix app. Searches many
on-demand providers (films / TV series) and downloads + transcodes
the resolved HLS stream into a PSP-compatible MP4.

Public API:
    from pspflix import PROVIDERS, list_providers, get_provider
    from pspflix.providers import Provider
    from pspflix.extractors import extract
"""
from .providers import (
    Provider,
    PROVIDERS,
    list_providers,
    get_provider,
    providers_by_language,
)
from .models import (
    Movie, TvShow, Episode, Season, Genre, People, Category, Video, Server,
)

__version__ = "1.0.1"
__app_name__ = "PSPflix"
