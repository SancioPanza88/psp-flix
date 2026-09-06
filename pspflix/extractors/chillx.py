"""Chillx extractor (ported from ChillxExtractor.kt).

Aliases for Jean included. Decrypts an embedded ciphertext using AES-CBC
with an OpenSSL-style (md5) key/iv derivation and a key fetched from a
remote keys file. Best-effort; returns Video(source="") on failure.
"""
import re
import json
import base64
import hashlib
from urllib.parse import urlparse
from .base import Extractor
from ..models import Video
from ._shared import SESSION, get_headers


KEYS_URL = "https://raw.githubusercontent.com/Rowdy-Avocado/multi-keys/keys/index.html"


def _hex_to_bytes(h: str) -> bytes:
    return bytes.fromhex(h)


def _generate_key_and_iv(password: bytes, salt: bytes, key_length=32, iv_length=16):
    md = hashlib.md5()
    target = key_length + iv_length
    generated = bytearray()
    prev = b""
    while len(generated) < target:
        md.update(prev + password + salt)
        prev = md.digest()
        generated.extend(prev)
    return bytes(generated[:key_length]), bytes(generated[key_length:target])


def _crypto_aes_handler(data: str, password: bytes) -> str:
    try:
        obj = json.loads(data)
        ct = obj["ct"]
        iv_hex = obj["iv"]
        s_hex = obj["s"]
        salt = _hex_to_bytes(s_hex)
        key, iv = _generate_key_and_iv(password, salt)
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        encrypted = base64.b64decode(ct)
        decrypted = decryptor.update(encrypted) + decryptor.finalize()
        pad = decrypted[-1]
        decrypted = decrypted[:-pad]
        return decrypted.decode("utf-8")
    except Exception as e:
        print(f"[Chillx AES Error] {e}")
        return ""


class ChillxExtractor(Extractor):
    main_url = "chillx.top"
    name = "Chillx"
    alias_urls = ["player.jeansaispasplus.homes", "moviesapi.club"]

    def extract(self, link: str, server=None) -> Video:
        try:
            parsed = urlparse(link)
            base = f"{parsed.scheme}://{parsed.netloc}"
            r = SESSION.get(link, headers=get_headers(referer=base), timeout=15)
            if r.status_code != 200:
                return Video(source="")
            html = r.text

            m = re.search(r"\s*=\s*'([^']+)", html)
            if not m:
                return Video(source="")
            content = m.group(1)

            kr = SESSION.get(KEYS_URL, timeout=15)
            if kr.status_code != 200:
                return Video(source="")
            keys = kr.json()
            chillx_keys = keys.get("chillx", [])
            if not chillx_keys:
                return Video(source="")
            key = chillx_keys[0].encode("utf-8")

            decrypt = _crypto_aes_handler(content, key)
            if not decrypt:
                return Video(source="")
            decrypt = decrypt.replace("\\n", "\n").replace("\\", "")

            fm = re.search(r'"?file"?:\\s*"([^"]+)', decrypt)
            if not fm:
                fm = re.search(r'"file"\s*:\s*"([^"]+)"', decrypt)
            if fm:
                return Video(source=fm.group(1), headers={"Referer": base}, referer=base)
        except Exception as e:
            print(f"[Chillx Error] {e}")
        return Video(source="")
