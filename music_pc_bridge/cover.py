"""Convert Windows media thumbnails to the ESP32 display's pixel format."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps

COVER_SIZE = (130, 130)
COVER_BYTES = COVER_SIZE[0] * COVER_SIZE[1] * 2
JPEG_MAX_BYTES = 32768


def _fit_cover(data: bytes) -> Image.Image:
    with Image.open(BytesIO(data)) as source:
        if source.width * source.height > 16_000_000:
            raise ValueError("thumbnail dimensions are unexpectedly large")
        oriented = ImageOps.exif_transpose(source)
        return ImageOps.fit(
            oriented.convert("RGB"), COVER_SIZE,
            method=Image.Resampling.LANCZOS,
        )


def convert_cover_jpeg(data: bytes) -> bytes:
    """Make a small baseline JPEG for the ESP32's version 3 cover decoder."""
    image = _fit_cover(data)
    output = BytesIO()
    # 4:4:4 preserves small album text and color edges at 130x130. The result
    # is typically several KiB instead of 33,800 bytes of raw RGB565.
    image.save(output, format="JPEG", quality=88, subsampling=0,
               optimize=True, progressive=False)
    result = output.getvalue()
    if not 4 <= len(result) <= JPEG_MAX_BYTES:
        raise ValueError(f"JPEG cover size {len(result)} is unsupported")
    return result


def convert_cover(data: bytes) -> bytes:
    """Center-crop and encode an image as 130×130 RGB565 little-endian."""
    rgb = _fit_cover(data).tobytes()

    result = bytearray(COVER_BYTES)
    for source_pos in range(0, len(rgb), 3):
        r, g, b = rgb[source_pos : source_pos + 3]
        color = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        target_pos = (source_pos // 3) * 2
        result[target_pos] = color & 0xFF
        result[target_pos + 1] = color >> 8
    return bytes(result)
