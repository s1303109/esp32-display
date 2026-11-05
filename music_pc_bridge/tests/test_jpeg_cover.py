"""Check the small JPEG cover and its version 3 BLE frame sequence."""

import asyncio
from io import BytesIO
from pathlib import Path
import struct
import sys
import unittest
import zlib

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cover import COVER_BYTES, JPEG_MAX_BYTES, convert_cover_jpeg
from test_controls import bridge
from wire import JPEG_COVER_VERSION


class FakeClient:
    def __init__(self):
        self.writes = []

    async def write_gatt_char(self, _uuid, payload, response):
        self.writes.append((bytes(payload), response))


class JPEGCoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_jpeg_is_baseline_and_smaller_than_raw(self):
        image = Image.new("RGB", (240, 240))
        image.putdata([
            ((x * 7 + y * 3) % 256, (x * 2 + y * 9) % 256,
             (x * 5 + y * 11) % 256)
            for y in range(240) for x in range(240)
        ])
        source = BytesIO()
        image.save(source, format="PNG")
        encoded = convert_cover_jpeg(source.getvalue())
        self.assertLess(len(encoded), COVER_BYTES)
        self.assertLessEqual(len(encoded), JPEG_MAX_BYTES)
        with Image.open(BytesIO(encoded)) as decoded:
            self.assertEqual(decoded.size, (130, 130))
            self.assertEqual(decoded.format, "JPEG")
            self.assertFalse(decoded.info.get("progressive", False))

        client = FakeClient()
        await bridge.send_jpeg_cover(client, encoded, 17, 244, fast=True)
        fragments = client.writes[:-1]
        self.assertTrue(fragments)
        self.assertTrue(all(not response for _, response in fragments))
        self.assertTrue(client.writes[-1][1])
        self.assertEqual(b"".join(frame[6:] for frame, _ in fragments), encoded)
        self.assertTrue(all(frame[0] == JPEG_COVER_VERSION for frame, _ in fragments))
        self.assertEqual(
            struct.unpack("<BBHHI", client.writes[-1][0]),
            (JPEG_COVER_VERSION, 17, len(encoded), len(encoded),
             zlib.crc32(encoded) & 0xFFFFFFFF),
        )


if __name__ == "__main__":
    unittest.main()
