"""Supervideo extractor (ported from SupervideoExtractor.kt)."""
import re
from urllib.parse import urlparse
from .base import Extractor
from ..models import Video
from ._shared import SESSION, get_headers, safe_get
from .jsunpacker import js_unpack


DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/121.0.0.0 Safari/537.36")


class SupervideoExtractor(Extractor):
    main_url = "supervideo.cc"
    name = "Supervideo"
    alias_urls = []

    def extract(self, link: str, server=None) -> Video:
        try:
            try:
                r = SESSION.get(link, headers={"User-Agent": DEFAULT_UA}, timeout=15,
                                allow_redirects=True)
            except Exception:
                if not link.startswith("http"):
                    link = "https:" + link
                r = SESSION.get(link, headers={"User-Agent": DEFAULT_UA}, timeout=15,
                                allow_redirects=True)
            if r.status_code != 200:
                return Video(source="")
            html = r.text

            idx = html.find("eval(function(p,a,c,k,e,d)")
            if idx == -1:
                return Video(source="")
            script_data = html[idx:]
            end = script_data.find("</script>")
            if end != -1:
                script_data = script_data[:end]
            script_data = "eval(function(p,a,c,k,e,d)" + script_data[len("eval(function(p,a,c,k,e,d)"):]

            unpacked = js_unpack(script_data)
            if not unpacked:
                return Video(source="")

            m = re.search(r'file\s*:\s*["\']([^"\']+)["\']', unpacked)
            if not m:
                return Video(source="")
            stream_url = m.group(1)
            return Video(source=stream_url, headers={"Referer": "mainUrl"},
                         referer=link)
        except Exception as e:
            print(f"[Supervideo Error] {e}")
        return Video(source="")
