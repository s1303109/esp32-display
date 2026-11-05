"""Disable this user's background music bridge without deleting its data."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from install_autostart import APP_NAME, LAUNCHER_NAME, require_folder, stop_background


def disable() -> Path:
    if os.name != "nt":
        raise RuntimeError("This command must run on Windows")

    destination = require_folder("LOCALAPPDATA") / APP_NAME
    launcher = (
        require_folder("APPDATA")
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
        / LAUNCHER_NAME
    )
    launcher.unlink(missing_ok=True)
    stop_background(destination)
    return destination


if __name__ == "__main__":
    try:
        data_dir = disable()
    except Exception as error:
        print(f"Could not disable background bridge: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("Background connection stopped and autostart disabled.")
    print(f"Logs and data remain in {data_dir}")
