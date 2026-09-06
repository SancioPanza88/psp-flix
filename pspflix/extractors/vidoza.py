"""Vidoza extractor (ported from VidozaExtractor.kt)."""
from urllib.parse import urlparse
from .base import Extractor
from ..models import Video
from ._shared import safe_get, get_headers


class VidozaExtractor(Extractor):
    main_url = "vidoza.net"
    name = "Vidoza"
    alias_urls = ["videzz.net"]

    def extract(self, link: str, server=None) -> Video:
        try:
            parsed = urlparse(link)
            base = f"{parsed.scheme}://{parsed.netloc}"
            path = parsed.path
            r = safe_get(base + path, headers=get_headers(referer=base), timeout=15)
            if r.status_code != 200:
                return Video(source="")
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, "html.parser")
            source = soup.select_one("source")
            if source and source.get("src"):
                url = source["src"]
                return Video(source=url, headers={"Referer": link}, referer=link)
        except Exception as e:
            print(f"[Vidoza Error] {e}")
        return Video(source="")
