"""Exercise the Windows media actions without requiring BLE or WinRT on CI."""

import asyncio
from datetime import timedelta
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_bridge():
    bleak = ModuleType("bleak")
    bleak.BleakClient = object
    bleak.BleakScanner = object
    winrt = ModuleType("winrt")
    winrt.__path__ = []
    windows = ModuleType("winrt.windows")
    windows.__path__ = []
    media = ModuleType("winrt.windows.media")
    media.__path__ = []
    control = ModuleType("winrt.windows.media.control")
    control.GlobalSystemMediaTransportControlsSessionManager = object
    control.GlobalSystemMediaTransportControlsSessionPlaybackStatus = SimpleNamespace(
        PLAYING=4
    )
    storage = ModuleType("winrt.windows.storage")
    storage.__path__ = []
    streams = ModuleType("winrt.windows.storage.streams")
    streams.Buffer = object
    streams.InputStreamOptions = SimpleNamespace(NONE=0)
    cover = ModuleType("cover")
    cover.COVER_BYTES = 33800
    cover.COVER_SIZE = (130, 130)
    cover.JPEG_MAX_BYTES = 32768
    cover.convert_cover = lambda data: data
    cover.convert_cover_jpeg = lambda data: b"\xff\xd8" + data
    imports = {
        "bleak": bleak,
        "winrt": winrt,
        "winrt.windows": windows,
        "winrt.windows.media": media,
        "winrt.windows.media.control": control,
        "winrt.windows.storage": storage,
        "winrt.windows.storage.streams": streams,
        "cover": cover,
    }
    spec = importlib.util.spec_from_file_location("bridge_control_test", ROOT / "bridge.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, imports):
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(spec.name, None)
    return module


bridge = load_bridge()


class FakeSession:
    source_app_user_model_id = "Chrome.UserData.Profile1"

    def __init__(self, status):
        self.status = status
        self.calls = []
        self.title = "Track"
        self.artist = "Artist"
        self.seek_ticks = []
        self.seek_done = asyncio.Event()
        self.toggle_result = True
        self.toggle_error = None

    def get_playback_info(self):
        return SimpleNamespace(playback_status=self.status)

    async def try_skip_previous_async(self):
        self.calls.append("previous")
        return True

    async def try_skip_next_async(self):
        self.calls.append("next")
        return True

    async def try_play_async(self):
        self.calls.append("play")
        return True

    async def try_pause_async(self):
        self.calls.append("pause")
        return True

    async def try_toggle_play_pause_async(self):
        self.calls.append("toggle")
        if self.toggle_error is not None:
            raise self.toggle_error
        return self.toggle_result

    async def try_get_media_properties_async(self):
        return SimpleNamespace(title=self.title, artist=self.artist)

    def get_timeline_properties(self):
        return SimpleNamespace(start_time=timedelta(seconds=10),
                               end_time=timedelta(seconds=130))

    async def try_change_playback_position_async(self, ticks):
        self.seek_ticks.append(ticks)
        self.seek_done.set()
        return True


class FakeManager:
    def __init__(self, *sessions):
        self.sessions = sessions

    def get_sessions(self):
        return self.sessions


class ControlTests(unittest.IsolatedAsyncioTestCase):
    def test_notification_validation(self):
        self.assertEqual(bridge.parse_control_notification(b"\x01\x03"), bridge.CONTROL_NEXT)
        self.assertEqual(bridge.parse_control_notification(b"\x01\x04\x64"),
                         (bridge.CONTROL_VOLUME, 100))
        self.assertEqual(
            bridge.parse_control_notification(b"\x01\x05\x07\xe8\x03\x00\x00"),
            (bridge.CONTROL_SEEK, 7, 1000),
        )
        self.assertIsNone(bridge.parse_control_notification(b"\x01"))
        self.assertIsNone(bridge.parse_control_notification(b"\x02\x03"))
        self.assertIsNone(bridge.parse_control_notification(b"\x01\x09"))
        self.assertIsNone(bridge.parse_control_notification(b"\x01\x04\x65"))
        self.assertIsNone(bridge.parse_control_notification(b"\x01\x05\x07\xe8"))

    async def test_volume_targets_windows_master(self):
        with patch.object(bridge, "set_volume_percent", return_value=41) as setter:
            self.assertTrue(await bridge.execute_control(
                FakeManager(), (bridge.CONTROL_VOLUME, 41)))
        setter.assert_called_once_with(41)

    async def test_previous_and_next(self):
        session = FakeSession(status=4)
        manager = FakeManager(session)
        self.assertTrue(await bridge.execute_control(manager, bridge.CONTROL_PREVIOUS))
        self.assertTrue(await bridge.execute_control(manager, bridge.CONTROL_NEXT))
        self.assertEqual(session.calls, ["previous", "next"])

    async def test_play_pause_uses_toggle_even_if_status_is_stale(self):
        session = FakeSession(status=4)
        manager = FakeManager(session)
        self.assertTrue(await bridge.execute_control(manager, bridge.CONTROL_PLAY_PAUSE))
        # The session still reports PLAYING after the first command; using
        # that stale state would call pause twice instead of toggling twice.
        self.assertTrue(await bridge.execute_control(manager, bridge.CONTROL_PLAY_PAUSE))
        self.assertEqual(session.calls, ["toggle", "toggle"])

    async def test_play_pause_falls_back_when_toggle_method_is_unavailable(self):
        session = FakeSession(status=4)
        session.try_toggle_play_pause_async = None
        manager = FakeManager(session)
        self.assertTrue(await bridge.execute_control(manager, bridge.CONTROL_PLAY_PAUSE))
        session.status = 5
        self.assertTrue(await bridge.execute_control(manager, bridge.CONTROL_PLAY_PAUSE))
        self.assertEqual(session.calls, ["pause", "play"])

    async def test_play_pause_falls_back_when_toggle_is_rejected(self):
        session = FakeSession(status=4)
        session.toggle_result = False
        manager = FakeManager(session)
        self.assertTrue(await bridge.execute_control(manager, bridge.CONTROL_PLAY_PAUSE))
        session.status = 5
        self.assertTrue(await bridge.execute_control(manager, bridge.CONTROL_PLAY_PAUSE))
        self.assertEqual(session.calls, ["toggle", "pause", "toggle", "play"])

    async def test_play_pause_falls_back_when_toggle_raises(self):
        session = FakeSession(status=4)
        session.toggle_error = RuntimeError("simulated WinRT failure")
        self.assertTrue(await bridge.execute_control(
            FakeManager(session), bridge.CONTROL_PLAY_PAUSE))
        self.assertEqual(session.calls, ["toggle", "pause"])

    async def test_no_chrome_session_is_ignored(self):
        self.assertFalse(await bridge.execute_control(FakeManager(), bridge.CONTROL_NEXT))

    async def test_seek_uses_current_track_and_100ns_ticks(self):
        session = FakeSession(status=4)
        active = bridge.ActiveTrack(7, bridge.Metadata("Track", "Artist", "Album"))
        self.assertTrue(await bridge.execute_seek(
            FakeManager(session), bridge.PendingSeek(7, 5000), active))
        # The timeline starts at 10 s; requested elapsed time is another 5 s.
        self.assertEqual(session.seek_ticks, [150_000_000])

    async def test_seek_clamps_to_end_and_rejects_stale_track(self):
        session = FakeSession(status=4)
        active = bridge.ActiveTrack(7, bridge.Metadata("Track", "Artist", "Album"))
        self.assertFalse(await bridge.execute_seek(
            FakeManager(session), bridge.PendingSeek(8, 5000), active))
        self.assertEqual(session.seek_ticks, [])
        self.assertTrue(await bridge.execute_seek(
            FakeManager(session), bridge.PendingSeek(7, 999_999), active))
        self.assertEqual(session.seek_ticks, [1_300_000_000])

    async def test_seek_rejects_mismatched_chrome_song(self):
        session = FakeSession(status=4)
        session.title = "Another Song"
        active = bridge.ActiveTrack(7, bridge.Metadata("Track", "Artist", "Album"))
        self.assertFalse(await bridge.execute_seek(
            FakeManager(session), bridge.PendingSeek(7, 5000), active))
        self.assertEqual(session.seek_ticks, [])

    async def test_worker_keeps_command_order(self):
        session = FakeSession(status=4)
        queue = asyncio.Queue()
        task = asyncio.create_task(bridge.control_worker(FakeManager(session), queue))
        try:
            queue.put_nowait(bridge.CONTROL_NEXT)
            queue.put_nowait(bridge.CONTROL_PREVIOUS)
            await asyncio.wait_for(queue.join(), timeout=1)
            self.assertEqual(session.calls, ["next", "previous"])
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_worker_uses_latest_queued_volume(self):
        queue = asyncio.Queue()
        pending = bridge.PendingVolume(20)
        queue.put_nowait(pending)
        pending.percent = 55
        with patch.object(bridge, "set_volume_percent", return_value=55) as setter:
            task = asyncio.create_task(bridge.control_worker(FakeManager(), queue))
            try:
                await asyncio.wait_for(queue.join(), timeout=1)
                setter.assert_called_once_with(55)
                self.assertFalse(pending.queued)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

    async def test_worker_uses_latest_queued_seek(self):
        session = FakeSession(status=4)
        active = bridge.ActiveTrack(7, bridge.Metadata("Track", "Artist", "Album"))
        queue = asyncio.Queue()
        pending = bridge.PendingSeek(7, 1000)
        queue.put_nowait(pending)
        pending.position_ms = 5000
        task = asyncio.create_task(bridge.control_worker(FakeManager(session), queue, active))
        try:
            await asyncio.wait_for(queue.join(), timeout=1)
            await asyncio.wait_for(session.seek_done.wait(), timeout=1)
            self.assertEqual(session.seek_ticks, [150_000_000])
            self.assertFalse(pending.queued)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_timed_out_seek_does_not_block_following_pause(self):
        session = FakeSession(status=4)
        active = bridge.ActiveTrack(7, bridge.Metadata("Track", "Artist", "Album"))
        queue = asyncio.Queue()
        queue.put_nowait(bridge.PendingSeek(7, 5000))
        seek_started = asyncio.Event()
        seek_cancelled = asyncio.Event()

        async def blocked_seek(*_args):
            seek_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                seek_cancelled.set()
                raise

        with (patch.object(bridge, "execute_seek", side_effect=blocked_seek),
              patch.object(bridge, "SEEK_ACTION_TIMEOUT", 1.0)):
            task = asyncio.create_task(bridge.control_worker(
                FakeManager(session), queue, active))
            try:
                await asyncio.wait_for(seek_started.wait(), timeout=1)
                start = asyncio.get_running_loop().time()
                queue.put_nowait(bridge.CONTROL_PLAY_PAUSE)
                await asyncio.wait_for(queue.join(), timeout=0.5)
                self.assertLess(asyncio.get_running_loop().time() - start, 0.2)
                self.assertEqual(session.calls, ["toggle"])
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertTrue(seek_cancelled.is_set())
            self.assertFalse(any(
                not running.done() and running.get_coro().__name__ == "seek_worker"
                for running in asyncio.all_tasks()
            ))

    async def test_next_drains_old_seek_before_skipping_song(self):
        session = FakeSession(status=4)
        active = bridge.ActiveTrack(7, bridge.Metadata("Track", "Artist", "Album"))
        queue = asyncio.Queue()
        queue.put_nowait(bridge.PendingSeek(7, 5000))
        seek_started = asyncio.Event()
        seek_cancelled = asyncio.Event()

        async def blocked_seek(*_args):
            seek_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                seek_cancelled.set()
                raise

        with patch.object(bridge, "execute_seek", side_effect=blocked_seek):
            task = asyncio.create_task(bridge.control_worker(
                FakeManager(session), queue, active))
            try:
                await asyncio.wait_for(seek_started.wait(), timeout=1)
                queue.put_nowait(bridge.CONTROL_NEXT)
                await asyncio.wait_for(queue.join(), timeout=0.5)
                self.assertTrue(seek_cancelled.is_set())
                self.assertEqual(session.calls, ["next"])
                self.assertIsNone(active.track_id)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task


if __name__ == "__main__":
    unittest.main()
