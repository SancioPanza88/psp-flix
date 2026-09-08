"""Minimal P.A.C.K.E.R. JS unpacker (ported from utils/JsUnpacker.kt)."""
import re
import math


def js_unpack(packed_js: str):
    """Unpack a P.A.C.K.E.R. encoded javascript string. Returns str or None."""
    if not packed_js:
        return None
    try:
        p = re.compile(r"\}\s*\('(.*)',\s*(.*?),\s*(\d+),\s*'(.*?)'\.split\('\|'\)",
                       re.DOTALL)
        m = p.search(packed_js)
        if not m or m.groupCount() != 4:
            return None
        payload = m.group(1).replace("\\'", "'")
        radix = 36
        count = 0
        try:
            radix = int(m.group(2))
        except Exception:
            pass
        try:
            count = int(m.group(3))
        except Exception:
            pass
        symtab = m.group(4).split("|")
        if len(symtab) != count:
            return None

        unbase = Unbase(radix)
        regex = re.compile(r"\b\w+\b")
        decoded = payload
        replace_offset = 0
        for wm in regex.finditer(payload):
            word = wm.group(0)
            try:
                x = unbase.unbase(word)
            except Exception:
                break
            value = symtab[x] if 0 <= x < len(symtab) else None
            if value:
                start = wm.start() + replace_offset
                end = wm.end() + replace_offset
                decoded = decoded[:start] + value + decoded[end:]
                replace_offset += len(value) - len(word)
        return decoded
    except Exception:
        return None


class Unbase:
    ALPHABET_62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    ALPHABET_95 = (" !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                   "[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~")

    def __init__(self, radix: int):
        self.radix = radix
        self.alphabet = None
        self.dictionary = {}
        if radix > 36:
            if radix < 62:
                self.alphabet = self.ALPHABET_62[:radix]
            elif 63 <= radix <= 94:
                self.alphabet = self.ALPHABET_95[:radix]
            elif radix == 62:
                self.alphabet = self.ALPHABET_62
            elif radix == 95:
                self.alphabet = self.ALPHABET_95
            if self.alphabet:
                for i, ch in enumerate(self.alphabet):
                    self.dictionary[ch] = i

    def unbase(self, s: str) -> int:
        if self.alphabet is None:
            return int(s, self.radix)
        tmp = s[::-1]
        ret = 0
        for i, ch in enumerate(tmp):
            ret += int(self.radix ** i * self.dictionary.get(ch, 0))
        return ret
