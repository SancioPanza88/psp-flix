"""Goodstream / generic jwplayer embed extractor (ported from scrapers.py)."""
import re
from .base import Extractor
from ..models import Video
from ._shared import safe_get, get_headers


_GOODSTREAM_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


class GoodstreamExtractor(Extractor):
    main_url = "goodstream.uno"
    name = "Goodstream"
    alias_urls = ["goodstream.store", "goodstream.watch"]

    def extract(self, link: str, server=None) -> Video:
        try:
            r = safe_get(link, headers=get_headers(referer=link), timeout=15)
            if r.status_code != 200:
                return Video(source="")
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, "html.parser")
            for script in soup.find_all("script"):
                content = script.string or script.get_text() or ""
                if "jwplayer" in content and "file" in content:
                    m = re.search(r'file\s*:\s*["\']([^"\']+)["\']', content)
                    if m:
                        url = m.group(1).replace("\\/", "/")
                        print(f"[Goodstream] Found: {url}")
                        # StreamflixReborn passes ONLY the User-Agent to the
                        # player for goodstream — a Referer header triggers 403.
                        return Video(source=url,
                                     headers={"User-Agent": _GOODSTREAM_UA},
                                     referer=link)
        except Exception as e:
            print(f"[Goodstream Error] {e}")
        return Video(source="")
