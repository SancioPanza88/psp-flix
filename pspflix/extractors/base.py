"""Base extractor contract (mirrors StreamFlix Extractor)."""
from ..models import Video


class Extractor:
    #: Primary hostname, e.g. "voe.sx"
    main_url = ""
    #: Display name.
    name = ""
    #: Alias hostnames that also belong to this extractor.
    alias_urls: list = []
    #: Regexes matched against the compare-url to catch rotating domains.
    rotating_domain: list = []

    def extract(self, link: str, server=None) -> Video:
        raise NotImplementedError
