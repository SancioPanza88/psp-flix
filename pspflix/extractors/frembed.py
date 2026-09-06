"""Frembed extractor (ported from FrembedExtractor.kt).

The original Android class resolves a list of server links via its own API
rather than a single embed URL. For the PSPflix extractor contract we
implement a best-effort: fetch the given link and search for a direct
stream (m3u8/mp4). Returns Video(source="") on failure.
"""
import re
from urllib.parse import urlparse
from .base import Extractor
from ..models import Video
from ._shared import SESSION, get_headers


class FrembedExtractor(Extractor):
    main_url = "frembed.click"
    name = "Frembed"
    alias_urls = ["frembed.link", "frembed.to"]

    def extract(self, link: str, server=None) -> Video:
        try:
            parsed = urlparse(link)
            base = f"{parsed.scheme}://{parsed.netloc}"
            r = SESSION.get(link, headers=get_headers(referer=base), timeout=15,
                            allow_redirects=True)
            if r.status_code != 200:
                return Video(source="")
            html = r.text
            # Follow Location header redirects if present (the Android version
            # resolves server redirects via the Location header).
            location = r.headers.get("Location")
            if location:
                if location.startswith("//"):
                    location = "https:" + location
                return Video(source=location, headers={"Referer": base}, referer=base)

            m = re.search(r'file\s*:\s*["\']((?:https?:)?//[^"\']+\.(?:m3u8|mp4)[^"\']*)["\']',
                          html)
            if m:
                url = m.group(1)
                if url.startswith("//"):
                    url = "https:" + url
                return Video(source=url, headers={"Referer": base}, referer=base)
        except Exception as e:
            print(f"[Frembed Error] {e}")
        return Video(source="")
