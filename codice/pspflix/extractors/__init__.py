"""Universal link dispatcher (mirrors StreamFlix Extractor.extract).

Given a raw embed/player URL it finds the right Extractor by
hostname (or rotating-domain regex or server name) and returns a
resolved Video.
"""
import re
from .base import Extractor
from .voe import VoeExtractor
from .vixcloud import VixcloudExtractor
from .vidxgo import VidxGoExtractor
from .goodstream import GoodstreamExtractor
from .streamtape import StreamtapeExtractor
from .vidoza import VidozaExtractor
from .filemoon import FilemoonExtractor
from .streamwish import StreamWishExtractor
from .twoembed import TwoEmbedExtractor
from .doodla import DoodLaExtractor
from .mixdrop import MixDropExtractor
from .supervideo import SupervideoExtractor
from .upcloud import UpcloudExtractor
from .rabbitstream import RabbitstreamExtractor
from .vidplay import VidplayExtractor
from .streamruby import StreamrubyExtractor
from .frembed import FrembedExtractor
from .chillx import ChillxExtractor


_URL_RE = re.compile(r"^(https?://)?(www\.)?")
_DOMAIN_CORE_RE = re.compile(r"^(https?://)?(www\.)?(.*?)(\.[a-z]+)")


def _compare(url: str) -> str:
    return _URL_RE.sub("", url.lower())


def _domain_core(url: str) -> str:
    m = _DOMAIN_CORE_RE.match(url.lower())
    if m:
        return m.group(3)
    return _compare(url)


_EXTRACTORS = [
    RabbitstreamExtractor(),
    VixcloudExtractor(),
    VidxGoExtractor(),
    VoeExtractor(),
    StreamtapeExtractor(),
    VidozaExtractor(),
    FilemoonExtractor(),
    StreamWishExtractor(),
    TwoEmbedExtractor(),
    DoodLaExtractor(),
    MixDropExtractor(),
    SupervideoExtractor(),
    UpcloudExtractor(),
    VidplayExtractor(),
    StreamrubyExtractor(),
    FrembedExtractor(),
    ChillxExtractor(),
    GoodstreamExtractor(),
]


def extract(link: str, server=None) -> "Video":
    """Resolve `link` to a playable Video using the right extractor."""
    from ..models import Video

    final_link = link

    # Universal bridge resolution (mysync.mov/stream/ -> real host).
    if "mysync.mov/stream/" in final_link:
        try:
            from ._shared import SESSION, USER_AGENT
            r = SESSION.get(final_link, headers={
                "User-Agent": USER_AGENT}, allow_redirects=True, timeout=15)
            body = r.text
            for pat in ('window.location.replace("', 'window.location.href = "', 'src="'):
                idx = body.find(pat)
                if idx != -1:
                    start = idx + len(pat)
                    redir = body[start:body.find('"', start)]
                    if redir.startswith("http"):
                        final_link = redir
                        break
        except Exception:
            pass

    cmp = _compare(final_link)
    found = None

    # 1. exact main/alias host prefix
    for ex in _EXTRACTORS:
        if cmp.startswith(_compare(ex.main_url)):
            found = ex
            break
        for alias in ex.alias_urls:
            if cmp.startswith(_compare(alias)):
                found = ex
                break
        if found:
            break

    # 2. domain-core match (ignore TLD)
    if found is None:
        for ex in _EXTRACTORS:
            if _domain_core(ex.main_url) and cmp.startswith(_domain_core(ex.main_url)):
                found = ex
                break
            for alias in ex.alias_urls:
                if _domain_core(alias) and cmp.startswith(_domain_core(alias)):
                    found = ex
                    break
            if found:
                break

    # 3. rotating-domain regex
    if found is None:
        for ex in _EXTRACTORS:
            if any(rx.search(cmp) for rx in ex.rotating_domain):
                found = ex
                break

    # 4. server-name match
    if found is None and server is not None:
        sname = (server.name if hasattr(server, "name") else str(server)).lower()
        for ex in _EXTRACTORS:
            if ex.name.lower() and ex.name.lower() in sname:
                found = ex
                break

    if found is None:
        raise Exception(f"No extractor found for URL: {final_link}")

    return found.extract(final_link, server)
