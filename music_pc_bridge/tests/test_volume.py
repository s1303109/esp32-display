"""Core Audio volume policy tests that run without a Windows sound device."""

from contextlib import contextmanager
import ctypes
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import volume


@contextmanager
def fake_endpoint():
    yield ctypes.c_void_p(123)


class FakeCoreAudio:
    def __init__(self, level: float, muted: bool = False):
        self.level = level
        self.muted = muted
        self.calls = []

    def call(self, _endpoint, slot, *args, argtypes=()):
        self.calls.append(slot)
        if slot == volume._GET_MUTE:
            ctypes.cast(args[0], ctypes.POINTER(ctypes.c_int)).contents.value = self.muted
        elif slot == volume._GET_LEVEL_SCALAR:
            ctypes.cast(args[0], ctypes.POINTER(ctypes.c_float)).contents.value = self.level
        elif slot == volume._SET_LEVEL_SCALAR:
            self.level = args[0].value
        elif slot == volume._SET_MUTE:
            self.muted = bool(args[0])
        else:
            raise AssertionError(f"unexpected COM method {slot}")
        return 0


class VolumeTests(unittest.TestCase):
    def test_guid_layout_matches_windows_core_audio(self):
        self.assertEqual(ctypes.sizeof(volume._GUID), 16)
        self.assertEqual(volume._IID_IAUDIO_ENDPOINT_VOLUME.Data1, 0x5CDF2C82)

    def test_reads_default_output_level(self):
        fake = FakeCoreAudio(level=0.63)
        with patch.object(volume, "_endpoint_volume", fake_endpoint), \
             patch.object(volume, "_call", fake.call):
            self.assertEqual(volume.get_volume_percent(), 63)
        self.assertEqual(fake.calls, [volume._GET_MUTE, volume._GET_LEVEL_SCALAR])

    def test_muted_output_is_shown_as_zero(self):
        fake = FakeCoreAudio(level=0.63, muted=True)
        with patch.object(volume, "_endpoint_volume", fake_endpoint), \
             patch.object(volume, "_call", fake.call):
            self.assertEqual(volume.get_volume_percent(), 0)
        self.assertEqual(fake.calls, [volume._GET_MUTE])

    def test_setting_positive_level_unmutes_and_clamps(self):
        fake = FakeCoreAudio(level=0.4, muted=True)
        with patch.object(volume, "_endpoint_volume", fake_endpoint), \
             patch.object(volume, "_call", fake.call):
            self.assertEqual(volume.set_volume_percent(130), 100)
        self.assertEqual(fake.calls, [
            volume._SET_LEVEL_SCALAR, volume._SET_MUTE,
            volume._GET_MUTE, volume._GET_LEVEL_SCALAR,
        ])
        self.assertFalse(fake.muted)

    def test_zero_level_does_not_change_mute_state(self):
        fake = FakeCoreAudio(level=0.4, muted=True)
        with patch.object(volume, "_endpoint_volume", fake_endpoint), \
             patch.object(volume, "_call", fake.call):
            self.assertEqual(volume.set_volume_percent(-5), 0)
        self.assertTrue(fake.muted)
        self.assertNotIn(volume._SET_MUTE, fake.calls)

    def test_com_failure_is_reported(self):
        with self.assertRaisesRegex(OSError, "0x80004005"):
            volume._check_hresult(-2147467259, "test")


if __name__ == "__main__":
    unittest.main()
