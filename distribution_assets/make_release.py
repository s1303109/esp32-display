"""Build the Windows 11 music-display ZIP from the verified ESP-IDF build.

Run after ``idf.py build``. The firmware manifest is regenerated from the
current binaries, then every archive entry is checked after writing the ZIP.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from prepare_firmware import DEFAULT_BUILD, DEFAULT_DEST, prepare


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "music_pc_bridge"
DEFAULT_OUTPUT = ROOT / "music_display_windows11_v10_unicode.zip"
FILES = (
    "START_HERE.txt",
    "README.md",
    "FLASHING.md",
    "setup_music_display.cmd",
    "setup_music_display.ps1",
    "install_background.cmd",
    "disable_background.cmd",
    "start_music_display.cmd",
    "install_autostart.py",
    "background.pyw",
    "bridge.py",
    "cover.py",
    "glyphs.py",
    "timeline.py",
    "wire.py",
    "volume.py",
    "requirements.txt",
    "flash_firmware.cmd",
    "flash_firmware.py",
    "fonts/NotoSansCJK-Regular.ttc",
    "fonts/OFL.txt",
    "fonts/README.md",
    "firmware/bootloader.bin",
    "firmware/partition-table.bin",
    "firmware/music_lvgl_demo.bin",
    "firmware/manifest.json",
)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def make_release(output: Path, build: Path = DEFAULT_BUILD) -> Path:
    prepare(build, DEFAULT_DEST)
    manifest = json.loads((DEFAULT_DEST / "manifest.json").read_text(encoding="utf-8"))
    for image in manifest["images"]:
        path = DEFAULT_DEST / image["file"]
        if path.stat().st_size != image["size"] or sha256(path) != image["sha256"]:
            raise ValueError(f"Firmware manifest does not match {path}")

    sources = [(BRIDGE / name, f"music_pc_bridge/{name}") for name in FILES]
    missing = [str(source) for source, _ in sources if not source.is_file()]
    if missing:
        raise FileNotFoundError("Missing release files: " + ", ".join(missing))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for source, arcname in sources:
            archive.write(source, arcname)

    with ZipFile(output) as archive:
        expected = {arcname for _, arcname in sources}
        if set(archive.namelist()) != expected or archive.testzip() is not None:
            output.unlink(missing_ok=True)
            raise ValueError("ZIP verification failed")
        for source, arcname in sources:
            if hashlib.sha256(archive.read(arcname)).hexdigest() != sha256(source):
                output.unlink(missing_ok=True)
                raise ValueError(f"ZIP entry does not match source: {arcname}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--build", type=Path, default=DEFAULT_BUILD)
    arguments = parser.parse_args()
    result = make_release(arguments.output, arguments.build)
    print(f"{result} ({result.stat().st_size} bytes, SHA-256 {sha256(result)})")


if __name__ == "__main__":
    main()
