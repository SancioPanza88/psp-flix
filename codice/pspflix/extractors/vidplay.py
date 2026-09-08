"""Vidplay extractor (ported from VidplayExtractor.kt).

Aliases for MyCloud, VidplayOnline included.

Uses a keys.json hosted on GitHub plus an RC4-style encode of the id, then
GET /mediainfo/<encId> and decrypt the (possibly encrypted) result with the
same RC4 key. Best-effort; returns Video(source="") on failure.
"""
import re
import json
import base64
from urllib.parse import urlparse, urlencode
from .base import Extractor
from ..models import Video
from ._shared import SESSION, get_headers


class VidplayExtractor(Extractor):
    main_url = "vidplay.site"
    name = "Vidplay"
    alias_urls = ["mcloud.bz", "vidplay.online"]
    key_url = "https://raw.githubusercontent.com/Ciarands/vidsrc-keys/main/keys.json"

    @staticmethod
    def _rc4(key: str, data: bytes) -> bytes:
        key_bytes = key.encode("utf-8")
        s = list(range(256))
        j = 0
        for i in range(256):
            j = (j + s[i] + key_bytes[i % len(key_bytes)]) & 0xff
            s[i], s[j] = s[j], s[i]
        out = bytearray(len(data))
        i = j = 0
        for idx in range(len(data)):
            i = (i + 1) & 0xff
            j = (j + s[i]) & 0xff
            s[i], s[j] = s[j], s[i]
            t = (s[i] + s[j]) & 0xff
            out[idx] = data[idx] ^ s[t]
        return bytes(out)

    def _encode(self, key: str, v_id: str) -> str:
        decoded = self._rc4(key, v_id.encode("utf-8"))
        b64 = base64.b64encode(decoded).decode("utf-8")
        return b64.replace("/", "_").replace("+", "-")

    def _decode_data(self, key: str, data: str) -> str:
        standardized = data.replace("_", "/").replace("-", "+")
        decoded = base64.b64decode(standardized)
        raw = self._rc4(key, decoded)
        return raw.decode("utf-8", errors="ignore")

    def extract(self, link: str, server=None) -> Video:
        try:
            parsed = urlparse(link)
            base = f"{parsed.scheme}://{parsed.hostname}"

            vid_id = parsed.path.split("/")[-1].split("?")[0]
            query = parsed.query

            kr = SESSION.get(self.key_url, timeout=15)
            if kr.status_code != 200:
                return Video(source="")
            keys = kr.json()
            encrypt_keys = keys.get("encrypt", [])
            decrypt_keys = keys.get("decrypt", [])
            if len(encrypt_keys) < 3 or len(decrypt_keys) < 2:
                return Video(source="")

            enc_id = self._encode(encrypt_keys[1], vid_id)
            h = self._encode(encrypt_keys[2], vid_id)
            media_url = f"{base}/mediainfo/{enc_id}?{query}&autostart=true&ads=0&h={h}"

            mr = SESSION.get(media_url, headers={
                "User-Agent": get_headers()["User-Agent"],
                "Referer": link,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            }, timeout=15)
            if mr.status_code != 200:
                return Video(source="")
            data = mr.json()

            result = data.get("result")
            if isinstance(result, dict):
                sources = result.get("sources") or []
                if sources and sources[0].get("file"):
                    return Video(source=sources[0]["file"], headers={"Referer": link},
                                 referer=link)
            elif isinstance(result, str):
                decrypted = self._decode_data(decrypt_keys[1], result)
                try:
                    obj = json.loads(decrypted)
                except Exception:
                    # maybe URL-encoded
                    from urllib.parse import unquote
                    obj = json.loads(unquote(decrypted))
                sources = obj.get("sources") or []
                if sources and sources[0].get("file"):
                    return Video(source=sources[0]["file"], headers={"Referer": link},
                                 referer=link)
        except Exception as e:
            print(f"[Vidplay Error] {e}")
        return Video(source="")
