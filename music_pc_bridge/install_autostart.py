"""Install the bridge in this Windows user's Startup folder without admin rights."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


APP_NAME = "ESP32MusicDisplay"
MUTEX_NAME = "Local\\ESP32MusicDisplayBridge"
LAUNCHER_NAME = "ESP32 Music Display.vbs"
STOP_NAME = "stop.request"
SOURCE_FILES = (
    "background.pyw",
    "bridge.py",
    "cover.py",
    "timeline.py",
    "wire.py",
    "volume.py",
    "requirements.txt",
)


def require_folder(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Windows environment variable {name} is missing")
    return Path(value)


def vbs_quoted(value: str) -> str:
    return value.replace('"', '""')


def background_running() -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_mutex = kernel32.OpenMutexW
    open_mutex.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p)
    open_mutex.restype = ctypes.c_void_p
    handle = open_mutex(0x00100000, 0, MUTEX_NAME)
    if handle:
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_int
        close_handle(handle)
        return True
    error = ctypes.get_last_error()
    if error == 2:  # ERROR_FILE_NOT_FOUND: the named mutex does not exist.
        return False
    raise OSError(error, "OpenMutexW failed")


def stop_background(destination: Path, timeout: float = 45.0) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    (destination / STOP_NAME).write_text("stop\n", encoding="ascii")
    deadline = time.monotonic() + timeout
    while background_running():
        if time.monotonic() >= deadline:
            raise TimeoutError("Background bridge did not stop within 45 seconds")
        time.sleep(0.25)


def install() -> Path:
    if os.name != "nt":
        raise RuntimeError("This installer must run on Windows")

    source = Path(__file__).resolve().parent
    destination = require_folder("LOCALAPPDATA") / APP_NAME
    startup = (
        require_folder("APPDATA")
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        raise RuntimeError(f"Cannot find pythonw.exe next to {sys.executable}")

    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--user", "-r", str(source / "requirements.txt")],
        check=True,
    )

    destination.mkdir(parents=True, exist_ok=True)
    startup.mkdir(parents=True, exist_ok=True)
    stop_background(destination)
    for filename in SOURCE_FILES:
        shutil.copy2(source / filename, destination / filename)

    command = f'"{pythonw}" "{destination / "background.pyw"}"'
    launcher = startup / LAUNCHER_NAME
    launcher.write_text(
        'Set shell = CreateObject("WScript.Shell")\r\n'
        f'shell.Run "{vbs_quoted(command)}", 0, False\r\n',
        encoding="utf-16",
        newline="",
    )

    (destination / STOP_NAME).unlink(missing_ok=True)
    subprocess.Popen(
        [str(pythonw), str(destination / "background.pyw")],
        cwd=destination,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return destination


if __name__ == "__main__":
    try:
        installed_to = install()
    except Exception as error:
        print(f"Background setup failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Installed to {installed_to}")
    ctypes.windll.user32.MessageBoxW(
        None,
        "已安裝背景連線。請關閉舊版橋接程式視窗；背景版會接手，之後登入 Windows 也會自動連接 ESP32。",
        "ESP32 Music Display",
        0x40,
    )
