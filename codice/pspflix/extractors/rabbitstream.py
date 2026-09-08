"""Rabbitstream / Megacloud extractor (ported from RabbitstreamExtractor.kt).

Aliases for Megacloud, Dokicloud, PremiumEmbeding included.

Megacloud uses: load embed HTML -> extract token -> GET getSources with
id+token -> if encrypted, derive a real key from the player JS and decrypt
the AES-CBC sources. Best-effort; returns Video(source="") on failure.
"""
import re
import json
import base64
import hashlib
from urllib.parse import urlparse
from .base import Extractor
from ..models import Video
from ._shared import SESSION, get_headers


class RabbitstreamExtractor(Extractor):
    main_url = "rabbitstream.net"
    name = "Rabbitstream"
    alias_urls = [
        "megacloud.blog", "videostr.net", "dokicloud.one",
        "premiumembeding.cloud", "rabbitstream.net", "mcloud.bz",
    ]

    def _generate_key(self, salt: bytes, secret: bytes) -> bytes:
        def md5(inp: bytes) -> bytes:
            return hashlib.md5(inp).digest()
        output = md5(secret + salt)
        current = output
        while len(current) < 48:
            output = md5(output + secret + salt)
            current += output
        return current

    def _decrypt_source(self, key: str, sources_b64: str) -> str:
        try:
            data = base64.b64decode(sources_b64)
            salt = data[8:16]
            encrypted = data[16:]
            full_key = self._generate_key(salt, key.encode())
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            cipher = Cipher(algorithms.AES(full_key[:32]), modes.CBC(full_key[32:48]))
            decryptor = cipher.decryptor()
            decrypted = decryptor.update(encrypted) + decryptor.finalize()
            # strip PKCS7 padding
            pad = decrypted[-1]
            decrypted = decrypted[:-pad]
            return decrypted.decode("utf-8")
        except Exception as e:
            print(f"[Rabbitstream Decrypt Error] {e}")
            return ""

    def _get_token(self, html: str):
        m = re.search(
            r'\w+\s*=\s*\{[^}]*?(\w+):\s*"([^"]+)",\s*(\w+):\s*"([^"]+)"(?:,\s*(\w+):\s*"([^"]+)")?',
            html)
        if m:
            val1 = m.group(2)
            val2 = m.group(4)
            val3 = m.group(6) if m.lastindex and m.lastindex >= 6 else ""
            return val1 + val2 + val3
        m = re.search(r'"([A-Za-z0-9+/=]{10,})",\s*"([A-Za-z0-9+/=]{10,})"(?:,\s*"([A-Za-z0-9+/=]{10,})")?',
                      html)
        if m:
            return m.group(1) + m.group(2) + (m.group(3) or "")
        m = re.findall(r"[A-Za-z0-9+/=]{30,}", html)
        if m:
            return max(m, key=len)
        return None

    def _get_keys(self, script_url: str, host: str) -> list:
        try:
            v = int(__import__("time").time())
            r = SESSION.get(script_url, headers={"User-Agent": get_headers()["User-Agent"]},
                            params={"v": v}, timeout=15)
            if r.status_code != 200:
                return []
            script = r.text

            def matching_key(value: str):
                mm = re.search(rf",{value}=((?:0x)?([0-9a-fA-F]+))", script)
                if mm:
                    return mm.group(1).replace("0x", "")
                return None

            keys = []
            for case in re.finditer(
                    r"case\s*0x[0-9a-f]+:(?![^;]*=partKey)\s*\w+\s*=\s*(\w+)\s*,\s*\w+\s*=\s*(\w+);",
                    script):
                k1 = matching_key(case.group(1))
                k2 = matching_key(case.group(2))
                if k1 and k2:
                    try:
                        keys.append([int(k1, 16), int(k2, 16)])
                    except ValueError:
                        pass
            return keys
        except Exception as e:
            print(f"[Rabbitstream Keys Error] {e}")
            return []

    def extract(self, link: str, server=None) -> Video:
        try:
            parsed = urlparse(link)
            host_url = f"{parsed.scheme}://{parsed.hostname}/"
            embed_path = "/".join(parsed.path.strip("/").split("/")[:-1])

            r = SESSION.get(link, headers={"User-Agent": get_headers()["User-Agent"],
                                           "Referer": host_url}, timeout=15)
            if r.status_code != 200:
                return Video(source="")
            token = self._get_token(r.text)

            sources_url = f"{host_url}{embed_path}/getSources"
            params = {"id": parsed.path.strip("/").split("/")[-1], "_k": token}
            sr = SESSION.get(sources_url, headers={
                "User-Agent": get_headers()["User-Agent"],
                "Referer": link,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "*/*",
            }, params=params, timeout=15)
            if sr.status_code != 200:
                return Video(source="")
            data = sr.json()

            sources_field = data.get("sources")
            if isinstance(sources_field, list):
                file_url = sources_field[0].get("file") if sources_field else ""
                if file_url:
                    return Video(source=file_url, headers={"Referer": host_url},
                                 referer=host_url)
            else:
                # Encrypted path (Megacloud-like)
                script_url = f"{host_url}js/player/a/prod/e1-player.min.js"
                raw_keys = self._get_keys(script_url, host_url)
                if not raw_keys:
                    return Video(source="")
                sources_str = sources_field
                arr = list(sources_str)
                current = 0
                extracted = []
                for idx in raw_keys:
                    start = idx[0] + current
                    end = start + idx[1]
                    if 0 <= start < end <= len(arr):
                        extracted.append("".join(arr[start:end]))
                        for i in range(start, end):
                            arr[i] = " "
                        current += idx[1]
                real_key = "".join(extracted)
                cleaned = "".join(arr).replace(" ", "")
                decrypted = self._decrypt_source(real_key, cleaned)
                if decrypted:
                    obj = json.loads(decrypted)
                    files = obj.get("sources", [])
                    if files and files[0].get("file"):
                        return Video(source=files[0]["file"], headers={"Referer": host_url},
                                     referer=host_url)
        except Exception as e:
            print(f"[Rabbitstream Error] {e}")
        return Video(source="")
