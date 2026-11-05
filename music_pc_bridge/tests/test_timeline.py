"""Protocol checks using the Chrome timeline values observed on Windows 11."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import struct
import unittest

from timeline import PlaybackSource, project_playback, state_from_winrt


class TimelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.updated = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
        self.chrome = SimpleNamespace(
            state=state_from_winrt(4),
            start=timedelta(0),
            end=timedelta(minutes=2, seconds=11, microseconds=997800),
            position=timedelta(minutes=1, seconds=4, microseconds=738792),
            last_updated=self.updated,
            rate=1.0,
        )

    def test_chrome_playing_position_and_exact_packet(self) -> None:
        packet = project_playback(self.chrome, self.updated + timedelta(seconds=2.5))
        self.assertEqual((packet.elapsed_ms, packet.duration_ms), (67238, 131997))
        self.assertEqual(
            packet.pack(7), struct.pack("<BBBBII", 1, 7, 1, 1, 67238, 131997)
        )

    def test_paused_does_not_extrapolate(self) -> None:
        self.chrome.state = state_from_winrt(5)
        packet = project_playback(self.chrome, self.updated + timedelta(minutes=1))
        self.assertEqual(packet.elapsed_ms, 64738)
        self.assertEqual(packet.state, 2)

    def test_rate_start_offset_and_end_clamp(self) -> None:
        self.chrome.start = timedelta(seconds=10)
        self.chrome.end = timedelta(seconds=20)
        self.chrome.position = timedelta(seconds=15)
        self.chrome.rate = 2.0
        packet = project_playback(self.chrome, self.updated + timedelta(seconds=4))
        self.assertEqual((packet.elapsed_ms, packet.duration_ms), (10000, 10000))

    def test_missing_timeline_sends_invalid_flag_and_zero_times(self) -> None:
        packet = project_playback(PlaybackSource(state=2))
        self.assertEqual(packet.pack(9), struct.pack("<BBBBII", 1, 9, 2, 0, 0, 0))
        self.assertEqual(state_from_winrt(3), 3)
        self.assertEqual(state_from_winrt(2), 0)


if __name__ == "__main__":
    unittest.main()
