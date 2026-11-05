"""Safety checks for the optional firmware flasher; no serial hardware used."""

import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "distribution_assets"))

import flash_firmware
import prepare_firmware


class FirmwarePackageTests(unittest.TestCase):
    def test_payload_hash_detects_a_changed_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            build = base / "build"
            build.mkdir()
            (build / "bootloader").mkdir()
            (build / "partition_table").mkdir()
            files = {
                "bootloader/bootloader.bin": b"BOOT",
                "partition_table/partition-table.bin": b"PART",
                "music_lvgl_demo.bin": b"APP",
            }
            for name, content in files.items():
                (build / name).write_bytes(content)
            (build / "flasher_args.json").write_text(json.dumps({
                "flash_settings": prepare_firmware.EXPECTED_SETTINGS,
                "extra_esptool_args": {"chip": "esp32s3"},
                "flash_files": {
                    offset: source for offset, source, _ in prepare_firmware.EXPECTED_FILES
                },
            }), encoding="utf-8")
            prepare_firmware.prepare(build, base / "firmware")
            self.assertEqual(len(flash_firmware.load_payload(base)), 3)
            (base / "firmware" / "music_lvgl_demo.bin").write_bytes(b"APP BROKEN")
            with self.assertRaises(flash_firmware.FlashSetupError):
                flash_firmware.load_payload(base)

            (base / "main").mkdir()
            source = base / "main" / "main.c"
            source.write_text("// newer firmware source", encoding="utf-8")
            app_time = (build / "music_lvgl_demo.bin").stat().st_mtime
            os.utime(source, (app_time + 5, app_time + 5))
            with self.assertRaisesRegex(ValueError, "older than"):
                prepare_firmware.prepare(build, base / "firmware")

    def test_wrong_connected_chip_or_flash_size_stops(self):
        for output in (
            "Chip type: ESP32-C3\nDetected flash size: 16MB\n",
            "Chip type: ESP32-S3\nDetected flash size: 8MB\n",
        ):
            with self.subTest(output=output), patch.object(
                flash_firmware.subprocess, "run",
                return_value=SimpleNamespace(returncode=0, stdout=output, stderr=""),
            ):
                with self.assertRaises(flash_firmware.FlashSetupError):
                    flash_firmware.check_connected_board("COM7")

    def test_cancelled_confirmation_never_writes(self):
        fake_images = [(offset, Path(name)) for offset, name in flash_firmware.IMAGE_LAYOUT]
        with (patch.object(flash_firmware, "load_payload", return_value=fake_images),
              patch.object(flash_firmware, "os", SimpleNamespace(name="nt")),
              patch.object(flash_firmware, "ensure_esptool"),
              patch.object(flash_firmware, "check_image_headers"),
              patch.object(flash_firmware, "available_ports", return_value=[("COM7", "ESP32")]),
              patch.object(flash_firmware, "select_port", return_value="COM7"),
              patch.object(flash_firmware, "check_connected_board"),
              patch.object(flash_firmware, "confirm_flash", return_value=False),
              patch.object(flash_firmware.subprocess, "run") as subprocess_run):
            self.assertEqual(flash_firmware.run([]), 0)
            subprocess_run.assert_not_called()

    def test_write_command_has_only_expected_regions(self):
        images = [(offset, Path(name)) for offset, name in flash_firmware.IMAGE_LAYOUT]
        command = flash_firmware.write_command("COM7", images)
        self.assertEqual(command[-6:], [
            "0x0", "bootloader.bin", "0x8000", "partition-table.bin",
            "0x10000", "music_lvgl_demo.bin",
        ])
        self.assertNotIn("--erase-all", command)
        self.assertNotIn("--force", command)


if __name__ == "__main__":
    unittest.main()
