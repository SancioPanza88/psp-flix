"""PSPflix providers package.

Re-exports every provider class and builds the registry
(mirrors StreamFlix's Provider.Companion.providers map).

Dual-language providers (StreamingCommunity, Vavoo) are
instantiated once per language so the UI can list/select them.
"""
from .base import Provider
from .streamingcommunity import StreamingCommunityProvider

# single-language / dual-instance providers are appended by the port script.
from .sflix import SflixProvider
from .altadefinizione01 import Altadefinizione01Provider
from .animeworld import AnimeWorldProvider
from .cb01 import CB01Provider
from .guardaserie import GuardaSerieProvider
from .serienstream import SerienStreamProvider
from .ridomovies import RidomoviesProvider
from .anikoto import AnikotoProvider
from .wiflix import WiflixProvider
from .mstream import MStreamProvider
from .frenchanime import FrenchAnimeProvider
from .filmpalast import FilmpalastProvider
from .poseidonhd2 import PoseidonHD2Provider
from .cuevanaeu import CuevanaEuProvider
from .latanime import LatanimeProvider
from .doramasflix import DoramasflixProvider
from .cinecalidad import CineCalidadProvider
from .seriesflix import SeriesFlixProvider
from .flixlatam import FlixLatamProvider
from .lacartoons import LaCartoonsProvider
from .animefenix import AnimefenixProvider
from .animeflv import AnimeFlvProvider
from .animeav1 import AnimeAv1Provider
from .animeonlineninja import AnimeOnlineNinjaProvider
from .sololatino import SoloLatinoProvider
from .cine24h import Cine24hProvider
from .pelisplusto import PelisplustoProvider
from .pelisflixhd import PelisflixHdProvider
from .cablevisionhd import CableVisionHDProvider
from .guardaflix import GuardaFlixProvider
from .animeunity import AnimeUnityProvider
from .animesaturn import AnimeSaturnProvider
from .frenchstream import FrenchStreamProvider
from .einschalten import EinschaltenProvider
from .hdfilme import HDFilmeProvider
from .megakino import MEGAKinoProvider
from .filmyonlinecc import FilmyOnlineCcProvider
from .zeriun import ZeriunProvider
from .tvporinternethd import TvporinternetHDProvider
from .frembed import FrembedProvider
from .kidraz import KidrazProvider
from .frenchmanga import FrenchMangaProvider
from .vavoo import VavooProvider
from .cinecity import CineCityProvider


DEFAULT_LANGUAGE = "en"


# ---------------------------------------------------------------------------
# ACTIVE providers.
#
# Only a small, curated set is enabled for now (one/two per language).
# ALL other providers remain fully implemented in this package and can be
# re-enabled later simply by adding their class to ENABLED_SINGLES (or the
# dual-language block below). Nothing has been deleted.
#
#   IT : AltaDefinizione, StreamingCommunity
#   EN : StreamingCommunity (EN), Sflix
#   ES : CuevanaEu
# ---------------------------------------------------------------------------

ENABLED_SINGLES = [
    Altadefinizione01Provider,     # it
    CuevanaEuProvider,             # es
]

# Providi disponibili ma non attivi (pronti per aggiornamenti futuri):
#   SflixProvider verificato pari all'upstream ma con search a 0 risultati
#   (probabile blocco Cloudflare/geo): tenuto disabilitato.
#   AnimeWorldProvider, CB01Provider, GuardaSerieProvider, SerienStreamProvider,
#   RidomoviesProvider, AnikotoProvider, WiflixProvider, MStreamProvider,
#   FrenchAnimeProvider, FilmpalastProvider, PoseidonHD2Provider, LatanimeProvider,
#   DoramasflixProvider, CineCalidadProvider, SeriesFlixProvider, FlixLatamProvider,
#   LaCartoonsProvider, AnimefenixProvider, AnimeFlvProvider, AnimeAv1Provider,
#   AnimeOnlineNinjaProvider, SoloLatinoProvider, Cine24hProvider, PelisplustoProvider,
#   PelisflixHdProvider, CableVisionHDProvider, GuardaFlixProvider, AnimeUnityProvider,
#   AnimeSaturnProvider, FrenchStreamProvider, EinschaltenProvider, HDFilmeProvider,
#   MEGAKinoProvider, FilmyOnlineCcProvider, ZeriunProvider, TvporinternetHDProvider,
#   FrembedProvider, KidrazProvider, FrenchMangaProvider, CineCityProvider, VavooProvider


def _build_registry():
    reg = {}

    # StreamingCommunity (it / en)
    sc_it = StreamingCommunityProvider(language="it")
    sc_en = StreamingCommunityProvider(language="en")
    reg[sc_it.name] = sc_it
    reg[sc_en.name] = sc_en

    for cls in ENABLED_SINGLES:
        inst = cls()
        reg[inst.name] = inst
    return reg


PROVIDERS = _build_registry()


def list_providers():
    return sorted(PROVIDERS.keys())


def get_provider(name: str) -> Provider:
    return PROVIDERS[name]


def providers_by_language(lang: str):
    return [(n, p) for n, p in PROVIDERS.items() if p.language == lang]
