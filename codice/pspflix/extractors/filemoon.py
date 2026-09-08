"""Filemoon extractor (ported from FilemoonExtractor.kt).

Filemoon uses an EC attestation handshake + AES-GCM decrypted playback.
This is a best-effort port of the Kotlin flow (challenge -> attest -> playback).
"""
import re
import json
import uuid
import base64
import hashlib
from urllib.parse import urlparse
from .base import Extractor
from ..models import Video
from ._shared import SESSION, get_headers


DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/147.0.0.0 Safari/537.36")


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _der_to_raw(der: bytes) -> bytes:
    offset = 2
    r_len = der[offset + 1]
    r = der[offset + 2:offset + 2 + r_len]
    if r and r[0] == 0:
        r = r[1:]
    offset += 2 + r_len
    s_len = der[offset + 1]
    s = der[offset + 2:offset + 2 + s_len]
    if s and s[0] == 0:
        s = s[1:]
    raw = bytearray(64)
    raw[32 - len(r):32] = r
    raw[64 - len(s):64] = s
    return bytes(raw)


def _strip_leading_zero(b: bytes) -> bytes:
    return b[1:] if b and b[0] == 0 else b


def _generate_attestation(nonce: str):
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_key = private_key.public_key()
        nums = public_key.public_numbers()
        x = _b64url(_strip_leading_zero(nums.x.to_bytes(32, "big")))
        y = _b64url(_strip_leading_zero(nums.y.to_bytes(32, "big")))
        der = private_key.sign(nonce.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
        raw = _der_to_raw(der)
        signature = _b64url(raw)
        jwk = {
            "crv": "P-256",
            "ext": True,
            "key_ops": ["verify"],
            "kty": "EC",
            "x": x,
            "y": y,
        }
        return signature, jwk
    except Exception as e:
        print(f"[Filemoon Attestation Error] {e}")
        return None, None


def _decrypt_playback(data: dict) -> str:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        iv = base64.urlsafe_b64decode(data["iv"] + "==")
        payload = base64.urlsafe_b64decode(data["payload"] + "==")
        p1 = base64.urlsafe_b64decode(data["key_parts"][0] + "==")
        p2 = base64.urlsafe_b64decode(data["key_parts"][1] + "==")
        key = p1 + p2
        aesgcm = AESGCM(key)
        decrypted = aesgcm.decrypt(iv, payload, None)
        return decrypted.decode("utf-8")
    except Exception as e:
        print(f"[Filemoon Decrypt Error] {e}")
        return ""


class FilemoonExtractor(Extractor):
    main_url = "filemoon.site"
    name = "Filemoon"
    alias_urls = ["bf0skv.org", "bysejikuar.com", "moflix-stream.link",
                  "bysezoxexe.com", "bysebuho.com", "filemoon.sx", "bysekoze.com",
                  "bysesayeveum.com"]

    def extract(self, link: str, server=None) -> Video:
        try:
            m = re.search(r"/(e|d)/([a-zA-Z0-9]+)", link)
            if not m:
                return Video(source="")
            link_type = m.group(1)
            video_id = m.group(2)
            current_domain = re.search(r"(https?://[^/]+)", link).group(1)

            hdrs = {
                "User-Agent": DEFAULT_UA,
                "Accept": "application/json",
            }
            details_url = f"{current_domain}/api/videos/{video_id}/embed/details"
            r = SESSION.get(details_url, headers=hdrs, timeout=15)
            if r.status_code != 200:
                return Video(source="")
            embed_frame_url = r.json().get("embed_frame_url")
            if not embed_frame_url:
                return Video(source="")
            playback_domain = re.search(r"(https?://[^/]+)", embed_frame_url).group(1)

            challenge_url = f"{playback_domain}/api/videos/access/challenge"
            ch = SESSION.post(challenge_url, headers={
                "Referer": embed_frame_url,
                "Origin": playback_domain,
                "User-Agent": DEFAULT_UA,
                "Accept": "application/json",
            }, json={}, timeout=15)
            if ch.status_code != 200:
                return Video(source="")
            challenge = ch.json()
            challenge_id = challenge.get("challenge_id")
            nonce = challenge.get("nonce")
            if not challenge_id or not nonce:
                return Video(source="")

            viewer_id = uuid.uuid4().hex
            device_id = uuid.uuid4().hex

            signature, jwk = _generate_attestation(nonce)
            if not signature:
                return Video(source="")

            attest_payload = {
                "viewer_id": viewer_id,
                "device_id": device_id,
                "challenge_id": challenge_id,
                "nonce": nonce,
                "signature": signature,
                "public_key": jwk,
                "client": {
                    "user_agent": DEFAULT_UA,
                    "architecture": "x86",
                    "bitness": "64",
                    "platform": "Windows",
                    "platform_version": "10.0.0",
                    "pixel_ratio": 1.0,
                    "screen_width": 1920,
                    "screen_height": 1080,
                    "languages": ["en-US"],
                },
                "storage": {
                    "cookie": viewer_id,
                    "local_storage": viewer_id,
                    "indexed_db": f"{viewer_id}:{device_id}",
                    "cache_storage": f"{viewer_id}:{device_id}",
                },
                "attributes": {"entropy": "high"},
            }
            attest = SESSION.post(f"{playback_domain}/api/videos/access/attest",
                                  headers={
                                      "Referer": embed_frame_url,
                                      "Origin": playback_domain,
                                      "User-Agent": DEFAULT_UA,
                                      "Accept": "application/json",
                                  },
                                  json=attest_payload, timeout=15)
            if attest.status_code != 200:
                return Video(source="")
            attest_data = attest.json()
            token = attest_data.get("token")
            confidence = attest_data.get("confidence")
            if not token or confidence is None:
                return Video(source="")
            viewer_id = attest_data.get("viewer_id", viewer_id)
            device_id = attest_data.get("device_id", device_id)

            playback_payload = {
                "fingerprint": {
                    "token": token,
                    "viewer_id": viewer_id,
                    "device_id": device_id,
                    "confidence": confidence,
                }
            }
            playback = SESSION.post(f"{playback_domain}/api/videos/{video_id}/embed/playback",
                                    headers={
                                        "Referer": embed_frame_url,
                                        "Origin": playback_domain,
                                        "X-Embed-Parent": link if link_type == "e" else "",
                                        "User-Agent": DEFAULT_UA,
                                        "Accept": "application/json",
                                    },
                                    json=playback_payload, timeout=15)
            if playback.status_code != 200:
                return Video(source="")
            pb = playback.json().get("playback")
            if not pb:
                return Video(source="")
            decrypted = _decrypt_playback(pb)
            if not decrypted:
                return Video(source="")
            obj = json.loads(decrypted)
            sources = obj.get("sources")
            if not sources:
                return Video(source="")
            url = sources[0].get("url")
            if url:
                return Video(source=url,
                             headers={"Referer": embed_frame_url,
                                      "User-Agent": DEFAULT_UA,
                                      "Origin": playback_domain},
                             referer=embed_frame_url)
        except Exception as e:
            print(f"[Filemoon Error] {e}")
        return Video(source="")
