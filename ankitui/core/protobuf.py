"""A minimal protobuf wire-format reader/writer, enough for Anki's schema 18 blobs."""

from __future__ import annotations

import struct


class Message:
    """Decoded message: field number -> list of raw values."""

    def __init__(self, data: bytes = b""):
        self.fields: dict[int, list] = {}
        if data:
            self._decode(data)

    def _decode(self, data: bytes) -> None:
        pos = 0
        n = len(data)
        while pos < n:
            key, pos = read_varint(data, pos)
            num, wire = key >> 3, key & 7
            if wire == 0:
                val, pos = read_varint(data, pos)
            elif wire == 1:
                val = data[pos : pos + 8]
                pos += 8
            elif wire == 2:
                length, pos = read_varint(data, pos)
                val = data[pos : pos + length]
                pos += length
            elif wire == 5:
                val = data[pos : pos + 4]
                pos += 4
            else:
                raise ValueError(f"unsupported wire type {wire}")
            self.fields.setdefault(num, []).append((wire, val))

    def has(self, num: int) -> bool:
        return num in self.fields

    def int(self, num: int, default: int = 0) -> int:
        vals = self.fields.get(num)
        if not vals:
            return default
        wire, val = vals[-1]
        if wire == 0:
            return val
        if wire == 1:
            return struct.unpack("<q", val)[0]
        if wire == 5:
            return struct.unpack("<i", val)[0]
        return default

    def sint(self, num: int, default: int = 0) -> int:
        """Signed int64/int32 encoded as plain varint (two's complement)."""
        v = self.int(num, default)
        if v >= 1 << 63:
            v -= 1 << 64
        return v

    def zigzag(self, num: int, default: int = 0) -> int:
        v = self.int(num, default)
        return (v >> 1) ^ -(v & 1)

    def bool(self, num: int) -> bool:
        return bool(self.int(num, 0))

    def float(self, num: int, default: float = 0.0) -> float:
        vals = self.fields.get(num)
        if not vals:
            return default
        wire, val = vals[-1]
        if wire == 5:
            return struct.unpack("<f", val)[0]
        if wire == 1:
            return struct.unpack("<d", val)[0]
        return default

    def floats(self, num: int) -> list[float]:
        out: list[float] = []
        for wire, val in self.fields.get(num, []):
            if wire == 2:
                out.extend(struct.unpack(f"<{len(val) // 4}f", val))
            elif wire == 5:
                out.append(struct.unpack("<f", val)[0])
        return out

    def bytes(self, num: int) -> bytes:
        vals = self.fields.get(num)
        if not vals:
            return b""
        return vals[-1][1]

    def str(self, num: int) -> str:
        return self.bytes(num).decode("utf-8", errors="replace")

    def msg(self, num: int) -> "Message | None":
        vals = self.fields.get(num)
        if not vals:
            return None
        return Message(vals[-1][1])

    def msgs(self, num: int) -> list["Message"]:
        return [Message(v) for w, v in self.fields.get(num, []) if w == 2]


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def encode_varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 64
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def field_varint(num: int, value: int) -> bytes:
    return encode_varint(num << 3) + encode_varint(value)


def field_bytes(num: int, value: bytes) -> bytes:
    return encode_varint((num << 3) | 2) + encode_varint(len(value)) + value
