"""Exercise metadata identity and brief Chrome-session gaps without Windows BLE."""

import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from test_controls import bridge, FakeManager
from glyphs import GlyphAsset


class FakeServices:
    def __init__(self, glyph_available=True):
        self.glyph_available = glyph_available

    def get_characteristic(self, uuid):
        if uuid == bridge.CONTROL_CHAR_UUID:
            return None
        if uuid == bridge.GLYPH_CHAR_UUID and not self.glyph_available:
            return None
        return SimpleNamespace(properties=("write",))


class FakeClient:
    def __init__(self, glyph_available=True):
        self.services = FakeServices(glyph_available)
        self.mtu_size = 247
        self.is_connected = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def song(title, artist, album):
    return bridge.MediaReading(
        metadata=bridge.Metadata(title, artist, album),
        thumbnail=object(),
    )


class MetadataFlowTests(unittest.IsolatedAsyncioTestCase):
    async def run_readings(self, readings, *, grace=1.0, interval=0.001,
                           glyph_builder=None, glyph_available=True):
        items = iter(readings)

        async def read_chrome(_manager):
            try:
                return next(items)
            except StopIteration:
                raise asyncio.CancelledError

        events = []
        sent_metadata = AsyncMock(side_effect=lambda _client, _value, track_id:
                                  events.append(("metadata", track_id)))
        sent_glyphs = AsyncMock(side_effect=lambda _client, _asset, track_id,
                                           _frame_size, *, fast:
                                events.append(("glyph", track_id)))
        sent_playback = AsyncMock(return_value=(0, False, 0, 0, 50))
        sent_cover = AsyncMock()
        sent_jpeg_cover = AsyncMock()
        cleared_cover = AsyncMock()
        scanner = SimpleNamespace(
            find_device_by_name=AsyncMock(return_value=SimpleNamespace(name="ESP32"))
        )
        args = SimpleNamespace(
            device_name="ESP32-Music-Display",
            scan_timeout=0.1,
            once=False,
            interval=interval,
            thumbnail_out=None,
        )
        with ExitStack() as stack:
            stack.enter_context(patch.object(bridge, "BleakScanner", scanner))
            stack.enter_context(patch.object(bridge, "BleakClient",
                                             return_value=FakeClient(glyph_available)))
            stack.enter_context(patch.object(bridge, "read_chrome_media", side_effect=read_chrome))
            stack.enter_context(patch.object(bridge, "get_volume_percent", return_value=50))
            stack.enter_context(patch.object(bridge, "read_thumbnail",
                                             new=AsyncMock(return_value=("image/png", b"same-art"))))
            stack.enter_context(patch.object(bridge, "convert_cover", return_value=b"\0" * 33800))
            stack.enter_context(patch.object(bridge, "convert_cover_jpeg", return_value=b"\xff\xd8jpeg"))
            stack.enter_context(patch.object(bridge, "report_thumbnail_data"))
            stack.enter_context(patch.object(bridge, "send_metadata", new=sent_metadata))
            stack.enter_context(patch.object(bridge, "send_glyphs", new=sent_glyphs))
            if glyph_builder is not None:
                stack.enter_context(patch.object(bridge, "build_glyph_asset",
                                                 side_effect=glyph_builder))
            stack.enter_context(patch.object(bridge, "send_playback", new=sent_playback))
            stack.enter_context(patch.object(bridge, "send_cover", new=sent_cover))
            stack.enter_context(patch.object(bridge, "send_jpeg_cover", new=sent_jpeg_cover))
            stack.enter_context(patch.object(bridge, "clear_cover", new=cleared_cover))
            stack.enter_context(patch.object(bridge, "MISSING_SESSION_GRACE", grace))
            with self.assertRaises(asyncio.CancelledError):
                await bridge.bridge(args, FakeManager())
        sent_cover.assert_not_awaited()
        return sent_metadata, sent_jpeg_cover, cleared_cover, sent_glyphs, events

    async def test_album_correction_reuses_track_id_and_cover(self):
        sent, covers, clears, _glyphs, _events = await self.run_readings([
            song("Track A", "Singer A", ""),
            song("Track A", "Singer A", "Album A"),
            song("Track B", "Singer B", "Album B"),
        ])
        self.assertEqual([call.args[2] for call in sent.await_args_list], [0, 0, 1])
        self.assertEqual(covers.await_count, 2)
        self.assertEqual([call.args[2] for call in covers.await_args_list], [0, 1])
        clears.assert_not_awaited()

    async def test_short_empty_session_does_not_clear_or_retransfer(self):
        sent, covers, clears, _glyphs, _events = await self.run_readings([
            song("Track A", "Singer A", "Album A"),
            None,
            song("Track A", "Singer A", "Album A"),
        ])
        self.assertEqual([call.args[2] for call in sent.await_args_list], [0])
        self.assertEqual(covers.await_count, 1)
        clears.assert_not_awaited()

    async def test_new_song_after_short_gap_is_sent_immediately(self):
        sent, covers, clears, _glyphs, _events = await self.run_readings([
            song("Track A", "Singer A", "Album A"),
            None,
            song("Track B", "Singer B", "Album B"),
        ])
        self.assertEqual([call.args[2] for call in sent.await_args_list], [0, 1])
        self.assertEqual(covers.await_count, 2)
        clears.assert_not_awaited()

    async def test_persistent_empty_session_clears_after_grace(self):
        sent, covers, clears, _glyphs, _events = await self.run_readings([
            song("Track A", "Singer A", "Album A"),
            None,
            None,
        ], grace=0.001, interval=0.01)
        self.assertEqual([call.args[2] for call in sent.await_args_list], [0, 1])
        self.assertEqual(sent.await_args_list[1].args[1], bridge.Metadata("", "", ""))
        self.assertEqual(covers.await_count, 1)
        clears.assert_awaited_once()

    async def test_unicode_asset_precedes_metadata_and_album_correction(self):
        def build_asset(title, _artist, album):
            if title == "Track C":
                return None
            return GlyphAsset(b"GLY1\0\0" + title.encode() + album.encode(), 0)

        sent, covers, _clears, glyphs, events = await self.run_readings([
            song("眼底星空", "李聖傑", ""),
            song("眼底星空", "李聖傑", "夜"),
            song("telepatía", "Kali Uchis", ""),
            song("Track C", "Singer C", ""),
        ], glyph_builder=build_asset)
        self.assertEqual(events, [
            ("glyph", 0), ("metadata", 0),
            ("glyph", 0), ("metadata", 0),
            ("glyph", 1), ("metadata", 1),
            ("metadata", 2),
        ])
        self.assertEqual(glyphs.await_count, 3)
        self.assertEqual(covers.await_count, 3)
        self.assertEqual([call.args[2] for call in sent.await_args_list], [0, 0, 1, 2])

    async def test_old_firmware_without_glyph_characteristic_still_sends_metadata(self):
        sent, covers, _clears, glyphs, events = await self.run_readings([
            song("眼底星空", "李聖傑", ""),
        ], glyph_available=False)
        self.assertEqual(events, [("metadata", 0)])
        glyphs.assert_not_awaited()
        self.assertEqual(sent.await_count, 1)
        self.assertEqual(covers.await_count, 1)


if __name__ == "__main__":
    unittest.main()
