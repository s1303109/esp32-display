"""Package only the Windows bridge for an already-flashed ESP32 display."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "music_pc_bridge"
README = Path(__file__).with_name("pc_only_readme.txt")
DEFAULT_OUTPUT = ROOT / "music_display_windows11_v10_pc_only.zip"
FILES = (
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
    "fonts/NotoSansCJK-Regular.ttc",
    "fonts/OFL.txt",
    "fonts/README.md",
)


def make_release(output: Path) -> Path:
    sources = [(README, "music_pc_bridge/START_HERE.txt")]
    sources.extend((BRIDGE / name, f"music_pc_bridge/{name}") for name in FILES)
    missing = [str(source) for source, _ in sources if not source.is_file()]
    if missing:
        raise FileNotFoundError("Missing package files: " + ", ".join(missing))

    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for source, arcname in sources:
            archive.write(source, arcname)

    with ZipFile(output) as archive:
        if set(archive.namelist()) != {name for _, name in sources} or archive.testzip():
            output.unlink(missing_ok=True)
            raise ValueError("ZIP verification failed")
        for source, arcname in sources:
            if hashlib.sha256(archive.read(arcname)).digest() != hashlib.sha256(source.read_bytes()).digest():
                output.unlink(missing_ok=True)
                raise ValueError(f"ZIP entry differs from source: {arcname}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    options = parser.parse_args()
    path = make_release(options.output)
    print(f"{path} ({path.stat().st_size} bytes; SHA-256 {hashlib.sha256(path.read_bytes()).hexdigest()})")


if __name__ == "__main__":
    main()
