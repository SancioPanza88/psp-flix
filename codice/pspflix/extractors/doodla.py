"""DoodStream extractor (ported from DoodLaExtractor.kt).

Aliases for DoodLi, Dood (vide0.net) included.
"""
import re
from urllib.parse import urlparse
from .base import Extractor
from ..models import Video
from ._shared import SESSION, get_headers, safe_get


class DoodLaExtractor(Extractor):
    main_url = "dood.la"
    name = "DoodStream"
    alias_urls = [
        "dsvplay.com", "mikaylaarealike.com", "myvidplay.com",
        "playmogo.com", "do7go.com", "d000d.com",
        "dood.li", "vide0.net",
    ]
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

    def extract(self, link: str, server=None) -> Video:
        try:
            parsed = urlparse(link)
            base = f"{parsed.scheme}://{parsed.netloc}"

            embed_url = link.replace("/d/", "/e/")
            r = SESSION.get(embed_url, headers=get_headers(referer=link), timeout=15,
                            allow_redirects=True)
            if r.status_code != 200:
                return Video(source="")
            html = r.text
            final_url = r.url
            final_base = f"{urlparse(final_url).scheme}://{urlparse(final_url).netloc}"

            md5_path_m = re.search(r"/pass_md5/[^']*", html)
            if not md5_path_m:
                return Video(source="")
            md5_url = final_base + md5_path_m.group(0)
            md5_r = SESSION.get(md5_url, headers=get_headers(referer=final_url), timeout=15)
            if md5_r.status_code != 200:
                return Video(source="")
            video_prefix = md5_r.text
            token = md5_url.rsplit("/", 1)[-1]
            hash_table = "".join(__import__("random").choice(self.alphabet) for _ in range(10))
            url = f"{video_prefix}{hash_table}?token={token}"
            return Video(source=url, headers={"Referer": final_base}, referer=final_base)
        except Exception as e:
            print(f"[DoodLa Error] {e}")
        return Video(source="")
