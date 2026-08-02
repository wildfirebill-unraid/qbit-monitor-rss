"""Minimal bencode decoder used to extract the info-hash from a .torrent file.

The decoder tracks the exact byte span of each dictionary value so we can
SHA-1 the raw `info` dict bytes (that is what a torrent's info-hash is).
"""

import hashlib


def _decode(data: bytes, pos: int):
    """Recursively decode. Returns (value, next_pos, spans).

    `spans` is only returned for dicts: a dict mapping the raw key bytes to a
    (start, end) byte span of that key's *value*.
    """
    t = data[pos : pos + 1]
    if t == b"i":
        end = data.index(b"e", pos)
        return int(data[pos + 1 : end]), end + 1, None
    if t == b"l":
        pos += 1
        items = []
        while data[pos : pos + 1] != b"e":
            v, pos, _ = _decode(data, pos)
            items.append(v)
        return items, pos + 1, None
    if t == b"d":
        pos += 1
        items = []
        spans = {}
        while data[pos : pos + 1] != b"e":
            key, pos, _ = _decode(data, pos)
            vstart = pos
            v, pos, _ = _decode(data, pos)
            items.append((key, v))
            spans[key] = (vstart, pos)
        return items, pos + 1, spans
    # byte string
    colon = data.index(b":", pos)
    length = int(data[pos:colon])
    raw = data[colon + 1 : colon + 1 + length]
    return raw, colon + 1 + length, None


def torrent_info_hash(raw: bytes) -> bytes | None:
    """Return the raw 20-byte SHA-1 info-hash of a .torrent file, or None."""
    if not raw:
        return None
    try:
        _value, _end, spans = _decode(raw, 0)
    except (ValueError, IndexError):
        return None
    if spans and b"info" in spans:
        start, end = spans[b"info"]
        return hashlib.sha1(raw[start:end]).digest()
    return None


def hex_info_hash(raw: bytes) -> str | None:
    h = torrent_info_hash(raw)
    return h.hex() if h else None
