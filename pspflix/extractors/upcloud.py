"""Upcloud extractor (best-effort port).

NOTE: No UpcloudExtractor.kt exists in the StreamFlix source. This is a
best-effort extractor modelled on the Megacloud/Rabbitstream family: it
loads the embed page, unpacks any Packer JS and searches for an m3u8/file.
Returns Video(source="") on failure.
"""
import re
from urllib.parse import urlparse
from .base import Extractor
from ..models import Video
from ._shared import SESSION, get_headers, safe_get
from .jsunpacker import js_unpack


class UpcloudExtractor(Extractor):
    main_url = "upcloud.to"
    name = "Upcloud"
    alias_urls = ["upcloud.bz", "upcloud.sx", "ucld.pro"]

    def extract(self, link: str, server=None) -> Video:
        try:
            parsed = urlparse(link)
            base = f"{parsed.scheme}://{parsed.netloc}"
            r = SESSION.get(link, headers=get_headers(referer=base), timeout=20,
                            allow_redirects=True)
            if r.status_code != 200:
                return Video(source="")
            html = r.text
            _, host = parsed.scheme, parsed.netloc

            packed = re.findall(r"<script[^>]*>(eval.*?)</script>", html, re.DOTALL)
            script = html
            for block in packed:
                u = js_unpack(block)
                if u:
                    script = u
                    break

            m = re.search(r'file\s*:\s*["\']((?:https?:)?//[^"\']+\.m3u8[^"\']*)["\']', script)
            if not m:
                m = re.search(r'["\']((?:https?:)?//[^"\']+\.m3u8[^"\']*)["\']', script)
            if m:
                url = m.group(1)
                if url.startswith("//"):
                    url = "https:" + url
                return Video(source=url, headers={"Referer": base}, referer=base)
        except Exception as e:
            print(f"[Upcloud Error] {e}")
        return Video(source="")
