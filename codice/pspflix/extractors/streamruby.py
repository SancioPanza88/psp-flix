"""Streamruby extractor (ported from StreamrubyExtractor.kt)."""
import re
from urllib.parse import urlparse
from .base import Extractor
from ..models import Video
from ._shared import SESSION, safe_get
from .jsunpacker import js_unpack


DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/122.0.0.0 Safari/537.36")


class StreamrubyExtractor(Extractor):
    main_url = "streamruby.com"
    name = "Streamruby"
    alias_urls = ["stmruby.com", "rubystm.com", "rubyvid.com", "moflix-stream.fans"]

    def extract(self, link: str, server=None) -> Video:
        try:
            parsed = urlparse(link)
            base = f"{parsed.scheme}://{parsed.netloc}"
            r = SESSION.get(link, headers={"User-Agent": DEFAULT_UA, "Referer": base},
                            timeout=15)
            if r.status_code != 200:
                return Video(source="")
            html = r.text

            packed = re.search(r"(eval\(function\(p,a,c,k,e,d\)(.|\n)*?)</script>", html)
            if not packed:
                return Video(source="")
            unpacked = js_unpack(packed.group(1))
            if not unpacked:
                return Video(source="")

            m = re.search(r'file\s*:\s*["\']([^"\']+)["\']', unpacked)
            if m:
                return Video(source=m.group(1), headers={"Referer": base}, referer=base)
        except Exception as e:
            print(f"[Streamruby Error] {e}")
        return Video(source="")
