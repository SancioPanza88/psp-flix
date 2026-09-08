"""2Embed extractor (ported from TwoEmbedExtractor.kt)."""
import re
from urllib.parse import urlparse
from .base import Extractor
from ..models import Video
from ._shared import safe_get, get_headers
from .streamwish import StreamWishExtractor


class TwoEmbedExtractor(Extractor):
    main_url = "2embed.cc"
    name = "2Embed"
    alias_urls = []

    def extract(self, link: str, server=None) -> Video:
        try:
            r = safe_get(link, headers=get_headers(referer=link), timeout=15)
            if r.status_code != 200:
                return Video(source="")
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, "html.parser")
            iframe = soup.select_one("iframe")
            if not iframe:
                return Video(source="")
            iframe_src = iframe.get("data-src") or iframe.get("src")
            if not iframe_src:
                return Video(source="")

            referer = iframe_src[:iframe_src.index("/", iframe_src.index("://") + 3)] \
                if "/" in iframe_src[iframe_src.index("://") + 3:] else iframe_src
            video_id = iframe_src.split("id=")[1].split("&")[0]
            final_url = f"https://uqloads.xyz/e/{video_id}"

            return StreamWishExtractor().extract(final_url, server)
        except Exception as e:
            print(f"[2Embed Error] {e}")
        return Video(source="")
