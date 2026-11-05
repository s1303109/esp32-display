"""BLE frame format shared with the ESP32 NimBLE server.

ATT's minimum MTU is 23 bytes, leaving 20 bytes for a write request.  Each
frame contains a six-byte header. Metadata uses version 1 and 20-byte writes.
Cover art supports version 1 reliable writes or version 2 write commands plus
one response-bearing CRC32 commit after MTU negotiation.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Iterator

VERSION = 1
FAST_COVER_VERSION = 2
HEADER_SIZE = 6
MAX_FRAME_SIZE = 20
MAX_PAYLOAD_SIZE = MAX_FRAME_SIZE - HEADER_SIZE
MAX_MESSAGE_SIZE = 512
FIELD_LIMITS = (192, 128, 128)  # ESP32 buffers: title[193], artist[129], album[129]


def _field(text: str, max_bytes: int) -> bytes:
    # NUL is the field separator. Trim by bytes, then drop any partial UTF-8
    # code point at the end of the trimmed value.
    data = text.replace("\x00", " ").encode("utf-8")[:max_bytes]
    return data.decode("utf-8", errors="ignore").encode("utf-8")


def encode_metadata(title: str, artist: str, album: str) -> bytes:
    """Encode three NUL-terminated UTF-8 fields, capped at 512 bytes."""
    fields = (
        _field(title, FIELD_LIMITS[0]),
        _field(artist, FIELD_LIMITS[1]),
        _field(album, FIELD_LIMITS[2]),
    )
    payload = b"\x00".join(fields) + b"\x00"
    if len(payload) > MAX_MESSAGE_SIZE:
        raise ValueError("metadata exceeds the protocol's 512-byte limit")
    return payload


def make_frames(
    payload: bytes,
    message_id: int,
    *,
    max_frame_size: int = MAX_FRAME_SIZE,
    max_message_size: int = MAX_MESSAGE_SIZE,
    allow_empty: bool = False,
    version: int = VERSION,
) -> Iterator[bytes]:
    """Yield in-order GATT write values for one complete message.

    Empty payloads are reserved for the cover characteristic's clear command.
    They produce exactly one six-byte header with total length zero.
    """
    if not 0 <= message_id <= 255:
        raise ValueError("message_id must be 0..255")
    if version not in (VERSION, FAST_COVER_VERSION):
        raise ValueError("unsupported frame version")
    if not HEADER_SIZE < max_frame_size <= 244:
        raise ValueError("max_frame_size must be 7..244 bytes")
    if not 1 <= max_message_size <= 65535:
        raise ValueError("max_message_size must be 1..65535 bytes")
    if not payload:
        if not allow_empty:
            raise ValueError("empty payload is only valid for a clear command")
        if version != VERSION:
            raise ValueError("fast cover frames cannot clear art")
        yield struct.pack("<BBHH", version, message_id, 0, 0)
        return
    if len(payload) > max_message_size:
        raise ValueError("payload exceeds this characteristic's size limit")

    total = len(payload)
    chunk_size = max_frame_size - HEADER_SIZE
    for offset in range(0, total, chunk_size):
        header = struct.pack("<BBHH", version, message_id, offset, total)
        yield header + payload[offset : offset + chunk_size]


def make_fast_cover_commit(payload: bytes, image_id: int) -> bytes:
    """Commit all version 2 cover fragments after the ESP verifies CRC32."""
    if not payload or len(payload) > 65535:
        raise ValueError("fast cover size must be 1..65535 bytes")
    if not 0 <= image_id <= 255:
        raise ValueError("image_id must be 0..255")
    size = len(payload)
    return struct.pack(
        "<BBHHI", FAST_COVER_VERSION, image_id, size, size,
        zlib.crc32(payload) & 0xFFFFFFFF,
    )
