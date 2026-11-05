"""Check the fast BLE cover protocol and its reliable fallback."""

import asyncio
import struct
import unittest
import zlib

from test_controls import bridge
from wire import FAST_COVER_VERSION, make_fast_cover_commit, make_frames


class FakeClient:
    is_connected = True

    def __init__(self, fail_commit=False):
        self.fail_commit = fail_commit
        self.writes = []

    async def write_gatt_char(self, _uuid, payload, response):
        self.writes.append((bytes(payload), response))
        if self.fail_commit and response and len(payload) == 10 and payload[0] == 2:
            raise RuntimeError("simulated missing fast fragment")


class FastCoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_playback_volume_extension_keeps_legacy_prefix(self):
        client = FakeClient()
        key = await bridge.send_playback(client, None, 7, 65)
        payload = client.writes[-1][0]
        self.assertEqual(len(payload), 13)
        self.assertEqual(payload[12], 65)
        self.assertEqual(key[-1], 65)

        await bridge.send_playback(client, None, 7, None)
        self.assertEqual(client.writes[-1][0], payload[:12])

    def test_commit_contains_length_and_crc(self):
        payload = bytes(range(250))
        commit = make_fast_cover_commit(payload, 7)
        self.assertEqual(struct.unpack("<BBHHI", commit),
                         (FAST_COVER_VERSION, 7, 250, 250,
                          zlib.crc32(payload) & 0xFFFFFFFF))

    async def test_fast_fragments_commit_exact_cover(self):
        data = bytes(i % 256 for i in range(33800))
        client = FakeClient()
        await bridge.send_cover(client, data, 42, 244, fast=True)
        fragments = client.writes[:-1]
        self.assertTrue(all(not response for _, response in fragments))
        self.assertTrue(client.writes[-1][1])
        self.assertEqual(client.writes[-1][0], make_fast_cover_commit(data, 42))
        self.assertEqual(b"".join(frame[6:] for frame, _ in fragments), data)
        self.assertTrue(all(frame[0] == FAST_COVER_VERSION for frame, _ in fragments))

    async def test_failed_fast_commit_retries_reliable_path(self):
        data = bytes(i % 256 for i in range(33800))
        client = FakeClient(fail_commit=True)
        await bridge.send_cover(client, data, 42, 244, fast=True)
        fast_count = len(tuple(make_frames(data, 42, max_frame_size=244,
                                           max_message_size=33800, version=2)))
        reliable = client.writes[fast_count + 1:]
        self.assertEqual(len(reliable), fast_count)
        self.assertTrue(all(response and frame[0] == 1
                            for frame, response in reliable))
        self.assertEqual(b"".join(frame[6:] for frame, _ in reliable), data)


if __name__ == "__main__":
    unittest.main()
