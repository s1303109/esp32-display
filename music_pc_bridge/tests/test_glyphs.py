"""Check the GLY1 glyph layout, Unicode rendering and BLE version-4 frames."""

import importlib.util
import struct
import unittest
import zlib

from test_controls import bridge
from glyphs import GLYPH_MAX_BYTES, GlyphAsset, build_glyph_asset
from wire import GLYPH_VERSION, make_frames


RECORD = struct.Struct("<BIBBBbbH")
HAVE_PILLOW = importlib.util.find_spec("PIL") is not None


def unpack_glyphs(asset: GlyphAsset):
    payload = asset.payload
    assert payload[:4] == b"GLY1"
    count = struct.unpack_from("<H", payload, 4)[0]
    records = []
    pos = 6
    for _ in range(count):
        values = RECORD.unpack_from(payload, pos)
        pos += RECORD.size
        font_px, codepoint, advance, width, height, offset_x, offset_y, length = values
        assert length == ((width + 1) // 2) * height
        bitmap = payload[pos:pos + length]
        assert len(bitmap) == length
        pos += length
        records.append((font_px, codepoint, advance, width, height,
                        offset_x, offset_y, bitmap))
    assert pos == len(payload)
    return records


class GlyphAssetTests(unittest.TestCase):
    def test_ascii_does_not_create_asset(self):
        self.assertIsNone(build_glyph_asset("Track", "Artist", "Album"))

    @unittest.skipUnless(HAVE_PILLOW, "Pillow is required for rasterization")
    def test_chinese_japanese_korean_and_accented_latin(self):
        samples = [
            ("眼底星空", "李聖傑", "", "眼"),
            ("telepatía", "Kali Uchis", "", "í"),
            ("世界に一つだけの花", "SMAP", "", "に"),
            ("너에게", "아이유", "", "너"),
        ]
        for title, artist, album, expected in samples:
            with self.subTest(title=title):
                asset = build_glyph_asset(title, artist, album)
                self.assertIsNotNone(asset)
                records = unpack_glyphs(asset)
                self.assertIn((20, ord(expected)),
                              [(record[0], record[1]) for record in records])
                self.assertTrue(any(record[-1] for record in records))
                self.assertEqual(asset.glyph_count, len(records))

    @unittest.skipUnless(HAVE_PILLOW, "Pillow is required for rasterization")
    def test_non_ascii_space_can_have_empty_bitmap(self):
        asset = build_glyph_asset("A\u00a0B", "", "")
        records = unpack_glyphs(asset)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][1], 0xA0)
        self.assertEqual(records[0][4], 0)
        self.assertEqual(records[0][-1], b"")
        self.assertGreater(records[0][2], 0)

    @unittest.skipUnless(HAVE_PILLOW, "Pillow is required for rasterization")
    def test_metadata_utf8_limits_are_used_before_rasterization(self):
        # 191 ASCII bytes plus the first byte of í would split a UTF-8 code
        # point; the wire encoder removes the incomplete character.
        self.assertIsNone(build_glyph_asset("A" * 191 + "í", "", ""))
        self.assertEqual(build_glyph_asset("A" * 190 + "í", "", "").glyph_count, 1)

    @unittest.skipUnless(HAVE_PILLOW, "Pillow is required for rasterization")
    def test_limit_preserves_title_artist_before_album(self):
        asset = build_glyph_asset("眼", "李", "星", max_bytes=355)
        codepoints = [record[1] for record in unpack_glyphs(asset)]
        self.assertEqual(codepoints, [ord("眼"), ord("李")])
        self.assertEqual(asset.omitted_count, 1)

    @unittest.skipUnless(HAVE_PILLOW, "Pillow is required for rasterization")
    def test_maximum_metadata_stays_within_esp_limits(self):
        title = "".join(chr(0x4E00 + i) for i in range(64))
        artist = "".join(chr(0x5000 + i) for i in range(42))
        album = "".join(chr(0x5100 + i) for i in range(42))
        asset = build_glyph_asset(title, artist, album)
        self.assertLessEqual(len(asset.payload), GLYPH_MAX_BYTES)
        self.assertLessEqual(asset.glyph_count, 192)
        self.assertIn((20, ord(title[0])),
                      [(r[0], r[1]) for r in unpack_glyphs(asset)])
        self.assertIn((14, ord(artist[0])),
                      [(r[0], r[1]) for r in unpack_glyphs(asset)])

    @unittest.skipUnless(HAVE_PILLOW, "Pillow is required for rasterization")
    def test_unique_two_byte_codepoints_hit_count_cap_after_primary_fields(self):
        characters = "".join(chr(0x0100 + i) for i in range(224))
        asset = build_glyph_asset(characters[:96], characters[96:160],
                                  characters[160:224])
        records = unpack_glyphs(asset)
        self.assertEqual(asset.glyph_count, 192)
        self.assertEqual(asset.omitted_count, 32)
        self.assertLessEqual(len(asset.payload), GLYPH_MAX_BYTES)
        self.assertIn(ord(characters[0]), [r[1] for r in records])
        self.assertIn(ord(characters[96]), [r[1] for r in records])


class FakeClient:
    is_connected = True

    def __init__(self, fail_first_commit=False):
        self.writes = []
        self.fail_first_commit = fail_first_commit

    async def write_gatt_char(self, uuid, data, response):
        self.writes.append((uuid, bytes(data), response))
        if self.fail_first_commit and response and len(data) == 10:
            self.fail_first_commit = False
            raise RuntimeError("simulated CRC rejection")


class GlyphWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_frames_and_crc_commit(self):
        asset = GlyphAsset(b"GLY1\x00\x00" + bytes(range(250)), 0)
        client = FakeClient()
        await bridge.send_glyphs(client, asset, 17, 244, fast=True)
        fragments = client.writes[:-1]
        self.assertTrue(all(uuid == bridge.GLYPH_CHAR_UUID and not response
                            for uuid, _frame, response in fragments))
        self.assertEqual([struct.unpack("<BBHH", frame[:6]) for _, frame, _ in fragments],
                         [(4, 17, 0, 256), (4, 17, 238, 256)])
        self.assertEqual(b"".join(frame[6:] for _, frame, _ in fragments),
                         asset.payload)
        uuid, commit, response = client.writes[-1]
        self.assertEqual(uuid, bridge.GLYPH_CHAR_UUID)
        self.assertTrue(response)
        self.assertEqual(struct.unpack("<BBHHI", commit),
                         (4, 17, 256, 256, zlib.crc32(asset.payload) & 0xFFFFFFFF))

    async def test_reliable_glyph_frames_still_use_crc(self):
        asset = GlyphAsset(b"GLY1\x00\x00" + b"A" * 300, 0)
        client = FakeClient()
        await bridge.send_glyphs(client, asset, 8, 20, fast=False)
        self.assertTrue(all(response for _uuid, _frame, response in client.writes))
        self.assertEqual(len(client.writes), len(tuple(make_frames(
            asset.payload, 8, max_frame_size=20,
            max_message_size=GLYPH_MAX_BYTES, version=GLYPH_VERSION))) + 1)

    async def test_fast_commit_failure_restarts_with_reliable_frames(self):
        asset = GlyphAsset(b"GLY1\x00\x00" + b"B" * 500, 0)
        client = FakeClient(fail_first_commit=True)
        await bridge.send_glyphs(client, asset, 5, 244, fast=True)
        first_count = len(tuple(make_frames(asset.payload, 5,
                                            max_frame_size=244,
                                            max_message_size=GLYPH_MAX_BYTES,
                                            version=GLYPH_VERSION)))
        reliable = client.writes[first_count + 1:]
        self.assertTrue(all(response for _uuid, _frame, response in reliable))
        self.assertEqual(b"".join(frame[6:] for _, frame, _ in reliable[:-1]),
                         asset.payload)


if __name__ == "__main__":
    unittest.main()
