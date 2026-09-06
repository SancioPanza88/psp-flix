"""MixDrop extractor (ported from MixDropExtractor.kt)."""
import re
from urllib.parse import urlparse
from .base import Extractor
from ..models import Video
from ._shared import SESSION, get_headers, safe_get
from .jsunpacker import js_unpack


DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0.0.0 Safari/537.36")


class MixDropExtractor(Extractor):
    main_url = "mixdrop.co"
    name = "MixDrop"
    alias_urls = [
        "mixdrop.bz", "mixdrop.ag", "mixdrop.ch", "mixdrop.to", "mixdrop.cv",
        "mxdrop.to", "mixdrop.club", "m1xdrop.net", "miiixdrop.net", "miixdrop.net",
    ]
    rotating_domain = [re.compile(r"^md[3bfyz][a-z0-9]*\.[a-z0-9]+", re.IGNORECASE)]

    def extract(self, link: str, server=None) -> Video:
        try:
            url = (link.replace("/f/", "/e/")
                   .replace(".club/", ".ag/"))
            url = re.sub(r"^(https?://[^/]+/e/[^/?#]+).*$", r"\1", url,
                         flags=re.IGNORECASE)

            headers = get_headers(referer="https://" + self.main_url)
            headers.update({
                "User-Agent": DEFAULT_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
                "X-Requested-With": "XMLHttpRequest",
            })
            r = SESSION.get(url, headers=headers, timeout=15, allow_redirects=True)
            if r.status_code != 200:
                return Video(source="")
            html = r.text

            packed = re.search(r"(eval\(function\(p,a,c,k,e,d\)(.|\n)*?)</script>", html)
            script = js_unpack(packed.group(1)) if packed else html
            if not script:
                script = html

            m = re.search(r'wurl.*?=.*?"(.*?)";', script)
            if not m:
                return Video(source="")
            source = m.group(1)
            if source.startswith("//"):
                source = "https:" + source
            elif source.startswith("http"):
                pass
            else:
                source = "https://" + source
            return Video(source=source, headers={"User-Agent": DEFAULT_UA},
                         referer=link)
        except Exception as e:
            print(f"[MixDrop Error] {e}")
        return Video(source="")
