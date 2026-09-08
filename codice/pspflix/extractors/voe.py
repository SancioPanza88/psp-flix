"""VOE / alias-host extractor (ported from scrapers.py)."""
import re
from urllib.parse import urlparse
from .base import Extractor
from ..models import Video
from ._shared import (
    SESSION, get_headers, safe_get, decrypt_voe, VOE_ALIAS_HOSTS,
)


class VoeExtractor(Extractor):
    main_url = "voe.sx"
    name = "VOE"
    alias_urls = [h.split("//")[1] for h in VOE_ALIAS_HOSTS]

    def extract(self, link: str, server=None) -> Video:
        parsed = urlparse(link)
        path_query = parsed.path + ("?" + parsed.query if parsed.query else "")
        urls_to_try = [link] + [f"{host}{path_query}" for host in VOE_ALIAS_HOSTS]

        html = ""
        for try_url in urls_to_try:
            try:
                r = SESSION.get(try_url, headers=get_headers(referer=link), timeout=10)
                if r.status_code == 200:
                    html = r.text
                    break
            except Exception:
                continue

        if not html:
            return Video(source="")

        soup = safe_get_html(html)
        encoded_str = None
        json_script = soup.find("script", type="application/json")
        if json_script:
            encoded_str = json_script.text.strip()
        else:
            m = re.search(r'<script\s+type="application/json">(.*?)</script>', html, re.DOTALL)
            if m:
                encoded_str = m.group(1).strip()

        if encoded_str:
            decrypted = decrypt_voe(encoded_str)
            if decrypted and "source" in decrypted:
                return Video(source=decrypted["source"],
                            headers={"Referer": link}, referer=link)

        print("[VOE] Falling back to regex search for m3u8...")
        m = re.search(r'["\']( https?://[^"\']+\.m3u8[^"\']*)["\']', html)
        if m:
            return Video(source=m.group(1), headers={"Referer": link}, referer=link)
        return Video(source="")


def safe_get_html(html):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")
