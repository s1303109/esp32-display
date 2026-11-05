"""Convert Windows media thumbnails to the ESP32 display's pixel format."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps

COVER_SIZE = (130, 130)
COVER_BYTES = COVER_SIZE[0] * COVER_SIZE[1] * 2


def convert_cover(data: bytes) -> bytes:
    """Center-crop and encode an image as 130×130 RGB565 little-endian."""
    with Image.open(BytesIO(data)) as source:
        if source.width * source.height > 16_000_000:
            raise ValueError("thumbnail dimensions are unexpectedly large")
        oriented = ImageOps.exif_transpose(source)
        image = ImageOps.fit(
            oriented.convert("RGB"), COVER_SIZE, method=Image.Resampling.LANCZOS
        )
        rgb = image.tobytes()

    result = bytearray(COVER_BYTES)
    for source_pos in range(0, len(rgb), 3):
        r, g, b = rgb[source_pos : source_pos + 3]
        color = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        target_pos = (source_pos // 3) * 2
        result[target_pos] = color & 0xFF
        result[target_pos + 1] = color >> 8
    return bytes(result)
