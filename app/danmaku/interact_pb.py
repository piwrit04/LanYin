# -*- coding: utf-8 -*-
"""Minimal protobuf decoder for Bilibili INTERACT_WORD_V2 (data.pb)."""
from __future__ import annotations

import base64
from typing import Any, Optional


def _read_varint(buf: bytes, i: int) -> tuple[Optional[int], int]:
    shift = 0
    n = 0
    length = len(buf)
    while i < length:
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        if b & 0x80 == 0:
            return n, i
        shift += 7
        if shift > 70:
            return None, i
    return None, i


def iter_fields(buf: bytes):
    i = 0
    n = len(buf)
    while i < n:
        key, i = _read_varint(buf, i)
        if key is None or i > n:
            return
        field, wire = key >> 3, key & 7
        if wire == 0:
            val, i = _read_varint(buf, i)
            if val is None:
                return
            yield field, val
        elif wire == 2:
            ln, i = _read_varint(buf, i)
            if ln is None or i + ln > n:
                return
            yield field, buf[i : i + ln]
            i += ln
        elif wire == 1:
            if i + 8 > n:
                return
            yield field, buf[i : i + 8]
            i += 8
        elif wire == 5:
            if i + 4 > n:
                return
            yield field, buf[i : i + 4]
            i += 4
        else:
            return


def decode_interact_pb(pb_b64: str) -> Optional[tuple[int, str, int]]:
    """Decode INTERACT_WORD_V2 `pb` into (uid, uname, msg_type)."""
    if not pb_b64 or not isinstance(pb_b64, str):
        return None
    try:
        raw = base64.b64decode(pb_b64)
    except Exception:
        return None
    if not raw:
        return None
    uid = 0
    uname = ""
    msg_type = 0
    uinfo_name = ""
    for field, val in iter_fields(raw):
        if field == 1 and isinstance(val, int):
            uid = val
        elif field == 2 and isinstance(val, (bytes, bytearray)):
            try:
                text = val.decode("utf-8").strip()
            except Exception:
                text = ""
            if text:
                uname = text
        elif field == 5 and isinstance(val, int):
            msg_type = val
        elif field == 22 and isinstance(val, (bytes, bytearray)) and not uname:
            uinfo_name = _name_from_uinfo(val)
    if not uname:
        uname = uinfo_name
    if not uname:
        return None
    return uid, uname, msg_type


def _name_from_uinfo(buf: bytes) -> str:
    # UserInfo.base is typically a nested message; look for short utf-8 strings.
    for _field, val in iter_fields(buf):
        if isinstance(val, (bytes, bytearray)):
            # nested Base { name = ... }
            inner = _first_utf8_name(val)
            if inner:
                return inner
    return _first_utf8_name(buf)


def _first_utf8_name(buf: bytes) -> str:
    for _field, val in iter_fields(buf):
        if not isinstance(val, (bytes, bytearray)) or not (2 <= len(val) <= 64):
            continue
        try:
            text = val.decode("utf-8").strip()
        except Exception:
            continue
        if text and "\x00" not in text:
            return text
    return ""


def find_pb_b64(data: dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("pb", "protobuf"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    inner = data.get("data")
    if isinstance(inner, dict):
        return find_pb_b64(inner)
    return ""


def encode_interact_pb(uid: int, uname: str, msg_type: int) -> str:
    """Test helper: encode a minimal InteractWordV2 payload."""
    def put_varint(n: int) -> bytes:
        out = bytearray()
        while n > 0x7F:
            out.append((n & 0x7F) | 0x80)
            n >>= 7
        out.append(n)
        return bytes(out)

    body = bytearray()
    body += put_varint((1 << 3) | 0) + put_varint(int(uid))
    name = (uname or "").encode("utf-8")
    body += put_varint((2 << 3) | 2) + put_varint(len(name)) + name
    body += put_varint((5 << 3) | 0) + put_varint(int(msg_type))
    return base64.b64encode(bytes(body)).decode("ascii")
