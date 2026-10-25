"""Small helpers mirroring Anki's id, checksum and text utilities."""

from __future__ import annotations

import hashlib
import html
import random
import re
import string
import time

_BASE91_TABLE = (
    string.ascii_letters + string.digits + "!#$%&()*+,-./:;<=>?@[]^_`{|}~"
)


def int_time(scale: int = 1) -> int:
    return int(time.time() * scale)


def guid64() -> str:
    num = random.randint(0, 2**64 - 1)
    buf = ""
    while num:
        num, i = divmod(num, len(_BASE91_TABLE))
        buf = _BASE91_TABLE[i] + buf
    return buf


_RE_STYLE = re.compile(r"(?si)<style.*?>.*?</style>")
_RE_SCRIPT = re.compile(r"(?si)<script.*?>.*?</script>")
_RE_TAG = re.compile(r"(?s)<.*?>")
_RE_IMG = re.compile(r"(?i)<img[^>]+src=[\"']?([^\"'>]+)[\"']?[^>]*>")


def strip_html(text: str) -> str:
    text = _RE_STYLE.sub("", text)
    text = _RE_SCRIPT.sub("", text)
    text = _RE_TAG.sub("", text)
    text = html.unescape(text)
    return text.strip()


def strip_html_media(text: str) -> str:
    """Strip HTML but keep media filenames, like Anki does for checksums."""
    text = _RE_IMG.sub(r" \1 ", text)
    return strip_html(text)


def field_checksum(text: str) -> int:
    digest = hashlib.sha1(strip_html_media(text).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def split_fields(flds: str) -> list[str]:
    return flds.split("\x1f")


def join_fields(fields: list[str]) -> str:
    return "\x1f".join(fields)


def fnv1a_64(*values: int) -> int:
    """64-bit FNV-1a over little-endian i64 values, as used by Anki for sort salts."""
    h = 0xCBF29CE484222325
    for v in values:
        for b in (v & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little"):
            h ^= b
            h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def parse_tags(tags: str) -> list[str]:
    return [t for t in tags.replace("　", " ").split() if t]


def join_tags(tags: list[str]) -> str:
    if not tags:
        return ""
    return " " + " ".join(tags) + " "


def canonify_tags(tags: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for tag in tags:
        tag = tag.strip()
        if not tag:
            continue
        seen.setdefault(tag.lower(), tag)
    return sorted(seen.values(), key=str.lower)
