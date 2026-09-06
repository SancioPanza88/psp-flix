"""VidxGo extractor — faithful port of VidxGoExtractor.kt.

Handles two cases:
  * TV episode URLs (contain "/t/"): the response is a JSON blob
    {"url": "...", "expire": ...}; we return that url.
  * Film / player URLs (https://v.vidxgo.co/{imdb}): the fifth
    (function(){...})() script carries `var k` + atob(...) which XOR-decrypt
    to a blob containing `currentSrc = "..."`.

The Video headers mirror exactly what StreamflixReborn passes to the player.
"""
import re
import base64
from .base import Extractor
from ..models import Video
from ._shared import SESSION, get_headers

_VIDXGO_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

_VIDXGO_PLAYER_HEADERS = {
    "Origin": "https://v.vidxgo.co",
    "Referer": "https://v.vidxgo.co/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Site": "cross-site",
    "User-Agent": _VIDXGO_UA,
}


class VidxGoExtractor(Extractor):
    main_url = "v.vidxgo.co"
    name = "VidxGo"

    def extract(self, link: str, server=None) -> Video:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(link)
            referer = f"{parsed.scheme}://{parsed.netloc}/"
            headers = {"Referer": referer, "User-Agent": _VIDXGO_UA}
            # Kotlin only sets sec-fetch-dest=iframe for NON "/t/" URLs.
            if "/t/" not in link:
                headers["Sec-Fetch-Dest"] = "iframe"

            r = SESSION.get(link, headers=headers, timeout=30)
            html = r.text or ""

            # --- TV episode endpoint: JSON {"url": "..."} ---
            if "/t/" in link:
                m = re.search(r'"url"\s*:\s*"([^"]+)"', html)
                if not m:
                    print("[VidxGo] TV series: no url field in response")
                    return Video(source="")
                video_url = m.group(1).replace("\\/", "/")
                return Video(source=video_url,
                             headers=dict(_VIDXGO_PLAYER_HEADERS),
                             referer=referer)

            # --- Film / player page: the k/atob-XOR blob containing
            # `currentSrc = "..."` moves around (was: 5th IIFE script, now a
            # big ~95KB block). Try every k/atob pair in every script.
            scripts = re.findall(r"<script[\s\S]*?</script>", html, re.IGNORECASE)
            tried = 0
            for target in scripts:
                keys = re.findall(r"var\s+k\s*=\s*['\"]([^'\"]+)['\"]", target)
                payloads = re.findall(r"atob\(['\"]([^'\"]+)['\"]\)", target)
                if not (keys and payloads):
                    continue
                for k in keys:
                    for d in payloads:
                        tried += 1
                        try:
                            decoded = base64.b64decode(d)
                        except Exception:
                            continue
                        decrypted = bytes(b ^ ord(k[i % len(k)])
                                          for i, b in enumerate(decoded))
                        text = decrypted.decode("utf-8", errors="ignore")
                        src_m = re.search(r"currentSrc\s*=\s*['\"]([^'\"]+)['\"]", text)
                        if src_m:
                            video_url = src_m.group(1).replace("\\/", "/")
                            return Video(source=video_url,
                                         headers=dict(_VIDXGO_PLAYER_HEADERS),
                                         referer=referer)
            print(f"[VidxGo] currentSrc not found after trying {tried} k/atob pair(s)")
            return Video(source="")
        except Exception as e:
            print(f"[VidxGo Error] {e}")
            return Video(source="")
