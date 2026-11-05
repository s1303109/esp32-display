"""Run the Windows music bridge without a console in the logged-in user session."""

from __future__ import annotations

import asyncio
import ctypes
from pathlib import Path
import os
import sys
import time
import traceback


APP_DIR = Path(__file__).resolve().parent
LOG_PATH = APP_DIR / "bridge.log"
STOP_PATH = APP_DIR / "stop.request"
MUTEX_NAME = "Local\\ESP32MusicDisplayBridge"
ERROR_ALREADY_EXISTS = 183


def open_log() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > 2 * 1024 * 1024:
        old_log = LOG_PATH.with_suffix(".log.1")
        old_log.unlink(missing_ok=True)
        LOG_PATH.replace(old_log)
    log = LOG_PATH.open("a", encoding="utf-8", buffering=1)
    sys.stdout = log
    sys.stderr = log


def acquire_single_instance() -> int | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    create_mutex.restype = ctypes.c_void_p
    handle = create_mutex(None, 0, MUTEX_NAME)
    if not handle:
        raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle(handle)
        return None
    return handle


async def run_until_stop(main) -> tuple[bool, int]:
    bridge_task = asyncio.create_task(main())
    while True:
        if STOP_PATH.exists():
            bridge_task.cancel()
            try:
                await bridge_task
            except asyncio.CancelledError:
                pass
            return True, 0
        if bridge_task.done():
            return False, bridge_task.result()
        await asyncio.sleep(0.25)


def close_mutex(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_int
    close_handle(handle)


def run_forever() -> None:
    mutex = None
    try:
        mutex = acquire_single_instance()
        if mutex is None:
            return
        open_log()
        print(f"Background bridge started; PID={os.getpid()}")
        os.chdir(APP_DIR)
        from bridge import main

        while True:
            if STOP_PATH.exists():
                print("Stop requested; background bridge exiting.")
                return
            try:
                stopped, result = asyncio.run(run_until_stop(main))
                if stopped:
                    print("Stop requested; background bridge exited.")
                    return
                print(f"Bridge stopped with code {result}; restarting in 5 seconds.")
            except Exception:
                traceback.print_exc()
                print("Bridge failed; restarting in 5 seconds.")
            for _ in range(20):
                if STOP_PATH.exists():
                    print("Stop requested; background bridge exiting.")
                    return
                time.sleep(0.25)
    except Exception:
        if sys.stderr is not None:
            traceback.print_exc()
    finally:
        if mutex is not None:
            close_mutex(mutex)


if __name__ == "__main__":
    run_forever()
