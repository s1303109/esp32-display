"""Build small, track-specific A4 bitmap glyphs for LVGL's Unicode fallback."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import struct

from wire import encode_metadata


FONT_PATH = Path(__file__).resolve().parent / "fonts" / "NotoSansCJK-Regular.ttc"
GLYPH_MAGIC = b"GLY1"
GLYPH_MAX_BYTES = 32768
GLYPH_MAX_COUNT = 192
_GLYPH_HEADER = struct.Struct("<BIBBBbbH")
_FACE_JAPANESE = 0
_FACE_KOREAN = 1
_FACE_TRADITIONAL_CHINESE = 3


@dataclass(frozen=True)
class GlyphAsset:
    payload: bytes
    glyph_count: int
    omitted_count: int = 0


def _font_face(text: str) -> int:
    """Choose regional CJK forms when the field identifies its script."""
    if any(0x3040 <= ord(c) <= 0x30FF or 0x31F0 <= ord(c) <= 0x31FF or
           0xFF66 <= ord(c) <= 0xFF9F for c in text):
        return _FACE_JAPANESE
    if any(0x1100 <= ord(c) <= 0x11FF or 0x3130 <= ord(c) <= 0x318F or
           0xA960 <= ord(c) <= 0xA97F or 0xAC00 <= ord(c) <= 0xD7FF
           for c in text):
        return _FACE_KOREAN
    return _FACE_TRADITIONAL_CHINESE


@lru_cache(maxsize=6)
def _font(face: int, size: int):
    # Pillow is already required for album art; importing lazily keeps the
    # bridge's no-Windows unit tests runnable without its native extension.
    from PIL import ImageFont

    if not FONT_PATH.is_file():
        raise FileNotFoundError(f"Bundled Unicode font is missing: {FONT_PATH}")
    return ImageFont.truetype(str(FONT_PATH), size, index=face)


def _pack_a4(image) -> bytes:
    """Pack each row independently, first pixel in the high nibble."""
    width, height = image.size
    if not width or not height:
        return b""
    pixels = image.tobytes()
    stride = (width + 1) // 2
    result = bytearray(stride * height)
    for y in range(height):
        row = y * width
        out = y * stride
        for x in range(width):
            alpha4 = (pixels[row + x] + 8) // 17
            if x & 1:
                result[out + x // 2] |= alpha4
            else:
                result[out + x // 2] = alpha4 << 4
    return bytes(result)


@lru_cache(maxsize=512)
def _rasterize(face: int, size: int, character: str) -> bytes:
    from PIL import Image, ImageDraw

    font = _font(face, size)
    bounds = font.getbbox(character, anchor="ls")
    left, top, right, bottom = bounds if bounds is not None else (0, 0, 0, 0)
    width = max(0, right - left)
    height = max(0, bottom - top)
    advance = int(font.getlength(character) + 0.5)
    offset_y = -bottom
    if not (0 <= advance <= 255 and 0 <= width <= 255 and
            0 <= height <= 255 and -128 <= left <= 127 and
            -128 <= offset_y <= 127):
        raise ValueError(f"Glyph metrics exceed BLE format: U+{ord(character):04X}")

    if width and height:
        image = Image.new("L", (width, height), 0)
        ImageDraw.Draw(image).text((-left, -top), character, font=font,
                                   fill=255, anchor="ls")
        bitmap = _pack_a4(image)
    else:
        # NBSP and some combining characters have advance but no box.
        bitmap = b""
    return _GLYPH_HEADER.pack(size, ord(character), advance, width, height,
                              left, offset_y, len(bitmap)) + bitmap


def build_glyph_asset(
    title: str, artist: str, album: str, *,
    max_bytes: int = GLYPH_MAX_BYTES,
    max_glyphs: int = GLYPH_MAX_COUNT,
) -> GlyphAsset | None:
    """Rasterize only code points present in the UTF-8 values sent to ESP32.

    Title and artist come first so extreme metadata lengths cannot crowd out
    the primary song information. Repeated code points at the same size are
    sent once. ASCII uses the firmware's existing LVGL fonts.
    """
    if max_bytes < 6 or not 0 <= max_glyphs <= GLYPH_MAX_COUNT:
        raise ValueError("invalid glyph asset limit")
    fields = encode_metadata(title, artist, album).split(b"\0")[:3]
    text_fields = tuple(field.decode("utf-8") for field in fields)
    records: list[bytes] = []
    seen: set[tuple[int, int]] = set()
    length = 6
    omitted = 0
    for text, size in zip(text_fields, (20, 14, 14), strict=True):
        face = _font_face(text)
        for character in text:
            if ord(character) < 128:
                continue
            key = size, ord(character)
            if key in seen:
                continue
            seen.add(key)
            record = _rasterize(face, size, character)
            if len(records) >= max_glyphs or length + len(record) > max_bytes:
                omitted += 1
                continue
            records.append(record)
            length += len(record)

    if not seen:
        return None
    payload = GLYPH_MAGIC + struct.pack("<H", len(records)) + b"".join(records)
    return GlyphAsset(payload, len(records), omitted)
