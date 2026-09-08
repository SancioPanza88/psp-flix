"""Streamtape extractor (ported from StreamtapeExtractor.kt)."""
import re
from .base import Extractor
from ..models import Video
from ._shared import SESSION, get_headers, safe_get, USER_AGENT


class StreamtapeExtractor(Extractor):
    main_url = "streamtape.com"
    name = "Streamtape"
    alias_urls = ["streamta.site"]

    def extract(self, link: str, server=None) -> Video:
        try:
            base = "https://" + self.main_url
            link_just_param = link.replace(base, "")
            r = safe_get(base + link_just_param,
                         headers=get_headers(referer=base, extra={"User-Agent": USER_AGENT}),
                         timeout=15)
            if r.status_code != 200:
                return Video(source="")
            html = r.text
        except Exception as e:
            print(f"[Streamtape Error] {e}")
            return Video(source="")

        try:
            script_regex = re.compile(
                r"document\.getElementById\('botlink'\)\.innerHTML\s*=\s*'([^']+)'\s*\+\s*\('([^']+)'\)\.substring\(([0-9]+)\)")
            m = script_regex.search(html)
            if not m:
                return Video(source="")
            base_url = m.group(1)
            param_string = m.group(2)
            substring_index = int(m.group(3))

            clean_params = param_string[substring_index:]

            video_id = re.search(r"id=([^&]+)", clean_params).group(1)
            expires = re.search(r"expires=([^&]+)", clean_params).group(1)
            ip = re.search(r"ip=([^&]+)", clean_params).group(1)
            token = re.search(r"token=([^&]+)", clean_params).group(1)

            final_video_url = f"{base}/get_video?id={video_id}&expires={expires}&ip={ip}&token={token}&stream=1"

            resp = SESSION.get(final_video_url, headers=get_headers(referer=base),
                               timeout=15, allow_redirects=False)
            location = resp.headers.get("Location")
            if location:
                return Video(source=location, headers={"Referer": link}, referer=link)
            if resp.url:
                return Video(source=resp.url, headers={"Referer": link}, referer=link)
        except Exception as e:
            print(f"[Streamtape Parse Error] {e}")
        return Video(source="")
