"""Optional, explicitly confirmed Windows flasher for the matching ESP32-S3 kit.

This program is separate from the background music bridge. It never flashes a
board until a COM port is selected, the chip and flash size are checked, and
the user types the final confirmation text.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ESPTOOL_VERSION = "5.6.0"
BOARD = "ESP32-S3-WROOM-1-N16R8"
DISPLAY = "ILI9341 320x240 SPI + XPT2046 touch"
FLASH_CONFIG = {"chip": "esp32s3", "mode": "dio", "frequency": "80m", "size": "16MB"}
IMAGE_LAYOUT = (("0x0", "bootloader.bin"),
                ("0x8000", "partition-table.bin"),
                ("0x10000", "music_lvgl_demo.bin"))
MANIFEST_NAME = "manifest.json"


class FlashSetupError(Exception):
    """The package or connected hardware is not suitable for this firmware."""


def load_payload(package_dir: Path) -> list[tuple[str, Path]]:
    """Check the complete package before installing tools or touching a port."""
    firmware_dir = package_dir / "firmware"
    manifest_path = firmware_dir / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FlashSetupError(
            "韌體檔案不完整，請先完整解壓 ZIP。"
        ) from exc

    if (not isinstance(manifest, dict) or
            manifest.get("format") != 1 or manifest.get("board") != BOARD or
            manifest.get("display") != DISPLAY or
            manifest.get("flash") != FLASH_CONFIG or
            not isinstance(manifest.get("images"), list) or
            len(manifest["images"]) != len(IMAGE_LAYOUT)):
        raise FlashSetupError("韌體清單與這組硬體不符。")

    images: list[tuple[str, Path]] = []
    for entry, (offset, filename) in zip(manifest["images"], IMAGE_LAYOUT):
        if (not isinstance(entry, dict) or
                entry.get("offset") != offset or entry.get("file") != filename or
                type(entry.get("size")) is not int or entry["size"] <= 0 or
                not isinstance(entry.get("sha256"), str) or
                re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None):
            raise FlashSetupError("韌體映像清單或雜湊格式有誤。")
        image_path = firmware_dir / filename
        try:
            actual_size = image_path.stat().st_size
            with image_path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError as exc:
            raise FlashSetupError(f"找不到韌體映像：{filename}") from exc
        if actual_size != entry["size"] or digest != entry["sha256"]:
            raise FlashSetupError(f"韌體映像 SHA-256 檢查失敗：{filename}")
        images.append((offset, image_path))
    return images


def ensure_esptool() -> None:
    """Install a fixed tool version only when the user runs this flasher."""
    try:
        installed = metadata.version("esptool")
    except metadata.PackageNotFoundError:
        installed = None
    if installed == ESPTOOL_VERSION:
        return
    print(f"正在安裝官方 esptool {ESPTOOL_VERSION}（僅目前 Windows 使用者）...")
    command = [sys.executable, "-m", "pip", "install", "--user",
               "--upgrade", f"esptool=={ESPTOOL_VERSION}"]
    subprocess.run(command, check=True)
    try:
        installed = metadata.version("esptool")
    except metadata.PackageNotFoundError:
        installed = None
    if installed != ESPTOOL_VERSION:
        raise FlashSetupError("目前 Python 無法使用指定版本的 esptool。")


def esptool_command(*args: str) -> list[str]:
    return [sys.executable, "-m", "esptool", *args]


def check_image_headers(images: list[tuple[str, Path]]) -> None:
    """Independently inspect both ESP image headers with esptool."""
    for _, image in (images[0], images[2]):
        result = subprocess.run(
            esptool_command("--chip", "esp32s3", "image-info", str(image)),
            check=True, capture_output=True, text=True, encoding="utf-8",
            errors="replace",
        )
        details = result.stdout
        required = ("ESP32-S3 Image Header", "Flash size: 16MB",
                    "Flash freq: 80m", "Flash mode: DIO",
                    "Chip ID: 9 (ESP32-S3)")
        if (any(item not in details for item in required) or
                re.search(r"Checksum: .* \(valid\)", details) is None or
                re.search(r"Validation hash: .* \(valid\)", details) is None):
            raise FlashSetupError(f"ESP32-S3 映像標頭有誤：{image.name}")


def available_ports() -> list[tuple[str, str]]:
    from serial.tools import list_ports

    ports = [(port.device, port.description or "Serial port")
             for port in list_ports.comports()
             if re.fullmatch(r"COM[0-9]+", port.device, re.IGNORECASE)]
    return sorted(ports, key=lambda item: int(item[0][3:]))


def select_port(ports: list[tuple[str, str]], requested: str | None) -> str:
    if not ports:
        raise FlashSetupError(
            "找不到 Windows COM 埠；請用 USB 接上 ESP32，並查看裝置管理員。"
        )
    print("\n偵測到的 COM 埠：")
    for number, (device, description) in enumerate(ports, 1):
        print(f"  {number}. {device}  {description}")

    if requested is None:
        choice = input("輸入 ESP32 所在 COM 埠的編號（直接按 Enter 取消）：").strip()
        if not choice:
            raise FlashSetupError("已取消，尚未連線到開發板。")
        if not choice.isdigit() or not 1 <= int(choice) <= len(ports):
            raise FlashSetupError("COM 埠編號無效。")
        return ports[int(choice) - 1][0]

    for device, _ in ports:
        if device.casefold() == requested.casefold():
            return device
    raise FlashSetupError(f"指定的 {requested!r} 不在已偵測的 COM 埠清單中。")


def check_connected_board(port: str) -> None:
    """Read-only esptool probe; fail closed for a different chip or flash size."""
    result = subprocess.run(
        esptool_command("--chip", "auto", "--port", port,
                        "--before", "default-reset", "flash-id"),
        check=False, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    print(result.stdout)
    if result.returncode != 0:
        raise FlashSetupError(
            "無法辨識這個 COM 埠上的開發板："
            + (result.stderr.strip() or result.stdout.strip())
        )
    if (re.search(r"Chip type:\s*ESP32-S3\b", result.stdout) is None or
            re.search(r"Detected flash size:\s*16MB\b", result.stdout) is None):
        raise FlashSetupError(
            "這個 COM 埠未確認為 ESP32-S3 加 16 MB Flash；尚未寫入任何資料。"
        )


def write_command(port: str, images: list[tuple[str, Path]]) -> list[str]:
    command = esptool_command(
        "--chip", "esp32s3", "--port", port, "--baud", "460800",
        "--before", "default-reset", "--after", "hard-reset",
        "write-flash", "--flash-mode", "dio", "--flash-freq", "80m",
        "--flash-size", "16MB",
    )
    for offset, image in images:
        command.extend((offset, str(image)))
    return command


def confirm_flash(port: str) -> bool:
    print(f"\n需要的開發板：{BOARD}")
    print(f"需要的螢幕：{DISPLAY}")
    print("這塊板子上的原有韌體將被覆寫。")
    print("繼續之前，請依 FLASHING.md 核對螢幕接線。")
    phrase = f"FLASH {port.upper()}"
    return input(f"輸入 {phrase} 才會開始燒錄（其他輸入皆取消）：").strip() == phrase


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="燒錄相同硬體的 ESP32-S3 音樂顯示器")
    parser.add_argument("--port", help="指定清單中的 COM 埠，仍需輸入最終確認詞")
    parser.add_argument("--check-package", action="store_true",
                        help="只核對映像雜湊，不安裝工具或連接開發板")
    args = parser.parse_args(argv)
    package_dir = Path(__file__).resolve().parent
    try:
        images = load_payload(package_dir)
        print("韌體包 SHA-256 檢查通過。")
        if args.check_package:
            return 0
        if os.name != "nt":
            raise FlashSetupError("這個一鍵燒錄工具只支援 Windows。")
        ensure_esptool()
        check_image_headers(images)
        port = select_port(available_ports(), args.port)
        check_connected_board(port)
        if not confirm_flash(port):
            print("已取消，尚未寫入任何資料。")
            return 0
        print(f"正在將已驗證的韌體寫入 {port}...")
        subprocess.run(write_command(port, images), check=True)
        print("esptool 已完成燒錄與驗證，開發板正在重新啟動。")
        return 0
    except (FlashSetupError, subprocess.CalledProcessError, OSError) as exc:
        print(f"燒錄未完成：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
