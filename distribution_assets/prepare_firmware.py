"""Copy the final ESP-IDF build into the optional Windows flashing payload.

Run this only after the final firmware build. The generated manifest binds the
three ESP-IDF binaries to SHA-256 hashes checked by flash_firmware.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUILD = ROOT / "music_lvgl_demo" / "build"
DEFAULT_DEST = ROOT / "music_pc_bridge" / "firmware"
EXPECTED_FILES = (
    ("0x0", "bootloader/bootloader.bin", "bootloader.bin"),
    ("0x8000", "partition_table/partition-table.bin", "partition-table.bin"),
    ("0x10000", "music_lvgl_demo.bin", "music_lvgl_demo.bin"),
)
EXPECTED_SETTINGS = {"flash_mode": "dio", "flash_freq": "80m", "flash_size": "16MB"}


def prepare(build: Path, destination: Path) -> Path:
    flash_args = json.loads((build / "flasher_args.json").read_text(encoding="utf-8"))
    if (flash_args.get("flash_settings") != EXPECTED_SETTINGS or
            flash_args.get("extra_esptool_args", {}).get("chip") != "esp32s3" or
            flash_args.get("flash_files") !=
            {offset: source for offset, source, _ in EXPECTED_FILES}):
        raise ValueError("ESP-IDF flash layout or target differs from the validated N16R8 build")

    # Refuse a stale app when the project is available beside the build tree.
    # The current working tree may still be edited after an older successful
    # build, so a valid-looking .bin alone does not prove it is the latest UI.
    project = build.parent
    source_files = [
        project / "sdkconfig", project / "sdkconfig.defaults",
        project / "CMakeLists.txt", project / "main" / "CMakeLists.txt",
        project / "components" / "music_ble" / "CMakeLists.txt",
    ]
    for source_dir in (project / "main", project / "components" / "music_ble"):
        if source_dir.is_dir():
            source_files.extend(path for path in source_dir.rglob("*")
                                if path.suffix in {".c", ".h"})
    source_files = [path for path in source_files if path.is_file()]
    app_image = build / "music_lvgl_demo.bin"
    if source_files and app_image.is_file():
        latest_source = max(source_files, key=lambda path: path.stat().st_mtime)
        if app_image.stat().st_mtime + 1 < latest_source.stat().st_mtime:
            raise ValueError(f"App binary is older than {latest_source}; rebuild first")

    destination.mkdir(parents=True, exist_ok=True)
    images = []
    for offset, relative_source, name in EXPECTED_FILES:
        source = build / relative_source
        if not source.is_file() or source.stat().st_size == 0:
            raise FileNotFoundError(f"Missing build image: {source}")
        target = destination / name
        shutil.copyfile(source, target)
        with target.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        images.append({
            "offset": offset,
            "file": name,
            "size": target.stat().st_size,
            "sha256": checksum,
        })

    manifest = {
        "format": 1,
        "board": "ESP32-S3-WROOM-1-N16R8",
        "display": "ILI9341 320x240 SPI + XPT2046 touch",
        "flash": {"chip": "esp32s3", "mode": "dio", "frequency": "80m", "size": "16MB"},
        "images": images,
    }
    path = destination / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=DEFAULT_BUILD)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DEST)
    options = parser.parse_args()
    print(prepare(options.build, options.destination))


if __name__ == "__main__":
    main()
