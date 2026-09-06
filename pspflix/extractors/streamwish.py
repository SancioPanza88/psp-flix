"""StreamWish extractor (ported from StreamWishExtractor.kt).

Aliases for Uqloads, Swish, Hlswish, Playerwish, SwiftPlayers included.
Uses Packer JS unpacking to locate the m3u8.
"""
import re
from urllib.parse import urlparse
from .base import Extractor
from ..models import Video
from ._shared import SESSION, get_headers, safe_get
from .jsunpacker import js_unpack


class StreamWishExtractor(Extractor):
    main_url = "streamwish.to"
    name = "Streamwish"
    alias_urls = [
        "streamwish.com", "streamwish.to", "ajmidyad.sbs", "khadhnayad.sbs",
        "yadmalik.sbs", "hayaatieadhab.sbs", "kharabnahs.sbs", "atabkhha.sbs",
        "atabknha.sbs", "atabknhk.sbs", "atabknhs.sbs", "abkrzkr.sbs", "abkrzkz.sbs",
        "wishembed.pro", "mwish.pro", "strmwis.xyz", "awish.pro", "dwish.pro",
        "vidmoviesb.xyz", "embedwish.com", "cilootv.store", "uqloads.xyz",
        "tuktukcinema.store", "doodporn.xyz", "ankrzkz.sbs", "volvovideo.top",
        "streamwish.site", "wishfast.top", "ankrznm.sbs", "sfastwish.com",
        "eghjrutf.sbs", "eghzrutw.sbs", "playembed.online", "egsyxurh.sbs",
        "egtpgrvh.sbs", "flaswish.com", "obeywish.com", "cdnwish.com", "javsw.me",
        "cinemathek.online", "trgsfjll.sbs", "fsdcmo.sbs", "anime4low.sbs",
        "mohahhda.site", "ma2d.store", "dancima.shop", "swhoi.com", "gsfqzmqu.sbs",
        "jodwish.com", "swdyu.com", "strwish.com", "asnwish.com", "wishonly.site",
        "playerwish.com", "katomen.store", "streamwish.fun", "swishsrv.com",
        "iplayerhls.com", "hlsflast.com", "4yftwvrdz7.sbs", "ghbrisk.com",
        "eb8gfmjn71.sbs", "cybervynx.com", "edbrdl7pab.sbs", "stbhg.click",
        "dhcplay.com", "gradehgplus.com", "ultpreplayer.com", "hglink.to",
        "haxloppd.com", "streamwish.club", "streamwish.cc", "streamwish.biz",
        "swish.site", "wishon.site", "vidwish.site", "awish.top", "dwish.top",
        "mwish.top", "streamwish.info", "streamwish.net", "streamwish.org",
        "streamwish.live", "streamwish.me", "swiftplayers.com", "swishsrv.com",
        "hlswish.com",
    ]

    def extract(self, link: str, server=None) -> Video:
        try:
            parsed = urlparse(link)
            referer = f"{parsed.scheme}://{parsed.netloc}/"

            # Follow redirects to the real /e/ embed page.
            r = SESSION.get(link, headers=get_headers(referer=referer), timeout=20,
                            allow_redirects=True)
            if r.status_code != 200:
                return Video(source="")
            redirected = r.url
            html = r.text

            # Find packed JS and unpack looking for m3u8.
            packed_matches = re.findall(
                r"<script[^>]*>(eval.*?)</script>", html, re.DOTALL)
            unpacked_script = ""
            for block in packed_matches:
                unpacked = js_unpack(block)
                if unpacked and "m3u8" in unpacked:
                    unpacked_script = unpacked
                    break

            script = unpacked_script or html

            source_regex = re.compile(
                '(?:"?hls(\\d*)"?|"?file"?)\\s*[:=]\\s*"((?:https?://|/)[^"\']+\\.m3u8[^"\']*)"'
            )

            found = []
            for sm in source_regex.finditer(script):
                idx = int(sm.group(1)) if sm.group(1) else 0
                url = sm.group(2)
                if url:
                    found.append((idx, url))
            found.sort(key=lambda x: x[0], reverse=True)
            source = found[0][1] if found else None

            if not source:
                m = re.search(r"file\s*:\s*['\"]([^'\"]+\.m3u8[^'\"]*)['\"]", script)
                if m:
                    source = m.group(1)

            if source:
                if source.startswith("/"):
                    host = urlparse(redirected).netloc
                    scheme = urlparse(redirected).scheme
                    source = f"{scheme}://{host}{source}"
                return Video(source=source, headers={
                    "Referer": referer,
                    "Origin": f"{urlparse(redirected).scheme}://{urlparse(redirected).netloc}",
                    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/131.0.0.0 Safari/537.36"),
                    "Accept": "*/*",
                }, referer=referer)
        except Exception as e:
            print(f"[StreamWish Error] {e}")
        return Video(source="")
