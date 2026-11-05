# Windows 11 Chrome media to ESP32 BLE bridge

繁體中文快速使用：Windows 11 64 位元電腦解壓完整 ZIP 後，雙擊 `setup_music_display.cmd`；它會檢查 Python 3.14，若缺少就從 Python 官方網站下載並安裝，再安裝所需套件與背景橋接程式。已安裝舊版可再次執行升級。之後 Windows 登入時會在背景自動連接 ESP32；在 Chrome 播放 YouTube Music，TFT 會更新歌名、封面與進度。右上角喇叭控制 Windows 主音量。需要停用時雙擊 `disable_background.cmd`。第一次安裝需要網路；日常播放不需要終端機。另一塊相同 ESP32-S3 如果尚未燒錄韌體，請先核對 [FLASHING.md](FLASHING.md) 的螢幕型號與接線，再使用選用的 `flash_firmware.cmd`。

This bridge reads Windows Global System Media Transport Controls (GSMTC),
selects the active Chrome session, and sends title, artist, album, cover art,
playback state, and timeline to the ESP32-S3 over BLE. The PC converts the
thumbnail to a 130×130 baseline JPEG for transfer; the ESP32 decodes it to
RGB565. A raw RGB565 compatibility path remains available. With the
matching touch firmware, the ESP32's previous, play/pause, and next buttons
send BLE notifications back to this bridge, which controls the Chrome media
session. Dragging the song progress bar sends a track-specific playback position
request to Chrome on release. The same notification path carries volume slider changes to Windows
Core Audio; the bridge reports the actual Windows master volume back with the
playback status. The ESP32 derives a solid dark background color from each
completed cover.

The matching firmware also displays Chinese, Japanese, Korean, and accented
Latin text in song metadata. This package keeps the licensed Noto Sans CJK
font on Windows and transfers only the glyphs needed for the current song.
The ESP32 continues to lay out the text with LVGL. The included font does not
cover every Unicode character, such as all color emoji; long fields are still
limited by the screen's label size and the UTF-8 byte caps below. Font source,
regional faces, and the OFL license are documented in [fonts/README.md](fonts/README.md).

Progress requests run separately from transport buttons, so pressing
play/pause immediately after a drag does not wait for the seek request to
finish. The bridge prefers GSMTC's play/pause toggle operation, with the
original explicit play/pause operations as a fallback if the toggle is rejected.

Requirements: Windows 11 x64, Chrome playing YouTube Music, Bluetooth LE
enabled, and matching ESP32-S3 firmware. The setup script installs Python
3.14 x64 and the pinned packages when needed, using the current Windows user.

## Background setup

After extracting the complete Windows package, double-click
`setup_music_display.cmd` once. `install_background.cmd` is an alias. The
installer downloads official Python 3.14.4 x64 only if a compatible Python is
not already available, verifies its SHA-256 and Windows Authenticode signature,
then installs the pinned Python packages. It copies the bridge to `%LOCALAPPDATA%\ESP32MusicDisplay`, starts it without a
console, and adds a launcher to this user's Startup folder. After each Windows
login, it searches for the ESP32, reconnects when the board is available, and
updates the display when Chrome/YouTube Music has a media session. A one-time
setup window may appear during installation; no terminal window needs to stay
open for everyday use. Close any older `start_music_display.cmd` window after
setup so the background bridge can take over the BLE connection.
The bridge displays Chrome's playback; it does not start playback itself.

The background log is `%LOCALAPPDATA%\ESP32MusicDisplay\bridge.log`.
To stop the background bridge and remove automatic startup, double-click
`disable_background.cmd`. Installed files and the log remain available.
Running `install_background.cmd` again updates the installed copy and restarts
the hidden bridge. Install the PC package matching the current ESP32 firmware;
an older bridge will not receive new control or volume commands. The TFT shows
`PC OFFLINE` when there is no subscribed PC.

`start_music_display.cmd` is a foreground fallback for diagnostics. Use one
launcher at a time.

For diagnostics, the same folder also supports:

```powershell
py -m pip install --user -r requirements.txt
py bridge.py --probe
```

The probe should list `Chrome.UserData.Profile1` (or another Chrome session),
the song fields, the thumbnail's content type and byte count, and whether it
can be converted to a 33,800-byte cover. To save the
raw thumbnail returned by Windows, add `--thumbnail-out .\thumbnail.bin`.
Its actual format is reported by the content type, usually JPEG or PNG.

After flashing the matching ESP32 BLE firmware:

```powershell
py bridge.py --once
py bridge.py
```

`--once` sends one track, status, and its cover, then exits. The regular command
monitors the track, sends metadata when the title or artist changes, checks the thumbnail every
one second, and transfers a cover only when its bytes change or after BLE
reconnection. An album name filled in later updates the same song ID without
resending the cover. A missing Chrome session is ignored for one second to
avoid clearing the screen during a brief song transition. Missing artwork sends a clear command. The bridge reconnects
after BLE interruption. Metadata is checked every 0.25 seconds by default;
playback state, progress, and master volume are sent when their displayed values
change. The bridge also sends a fresh status immediately after metadata and
after a cover transfer.
Press Ctrl+C to stop it. Use `--device-name NAME` if the ESP32 advertises a
different name.

## Wire format

- Device name: `ESP32-Music-Display`
- Service: `d1cb460f-4eea-4594-8a7a-b6da2cde9c9c`
- Metadata write characteristic: `e947ddcd-4fe0-44ca-a75d-049bb2edc7be`
- Cover write characteristic: `0e60fb2f-ea19-4df2-a32a-19f81145ac17`
- Unicode glyph write characteristic: `9e74967b-6875-4000-8477-f25b93b58861`
- Playback write characteristic: `9c950662-05e7-4e55-a39e-0658ccb0c8fb`
- Control notify characteristic: `4b717b37-4a03-4aa2-9dfc-a4bb88dfc905`
- Metadata and playback GATT writes use response. Metadata writes carry at
  most 20 bytes. Cover frames use the negotiated MTU minus 3 bytes, capped at
  244 bytes.
- Header: version `1` (u8), message ID (u8), offset (u16 little endian),
  total length (u16 little endian). The remainder is a payload slice.
- Reassembled payload: UTF-8 `title\0artist\0album\0`, at most 512 bytes.
- Cover payload: exactly 130×130 RGB565 little-endian pixels (33,800 bytes).
  A six-byte header with total length zero clears the art. Metadata and its
  cover use the same message ID, including a cover update that arrives later.
- Fast cover protocol: version `2` fragments use the same six-byte header and
  GATT write without response. A final ten-byte `<BBHHI>` write **with**
  response contains version `2`, image ID, offset and total length both
  `33800`, and little-endian CRC32 of the full RGB565 image. The ESP32 validates
  contiguous offsets, size, and CRC32 before displaying. If unavailable or the
  commit fails, the bridge retries version `1` write-with-response frames.
- JPEG cover protocol: version `3` carries a 130×130 baseline JPEG (maximum
  32,768 bytes) in the same six-byte frames and uses a response-bearing CRC32
  commit. The ESP32 decodes a complete verified JPEG to RGB565 before LVGL
  displays it. The bridge uses quality 88 and 4:4:4 color sampling; a reference
  cover required about 5–6 KiB instead of 33,800 bytes. If the firmware does
  not accept version `3`, the bridge falls back to the RGB565 protocol. The
  actual transfer time depends on the Windows BLE connection.
- Unicode glyph protocol: version `4` sends at most 192 track-specific
  monochrome glyphs and 32,768 bytes. The `GLY1` asset carries A4 bitmap
  masks for the title at 20 px and artist/album at 14 px. Fragments use the
  same six-byte little-endian header as cover v2/v3; a response-bearing
  ten-byte commit includes track ID, final size, and CRC32. The bridge sends
  this asset before the corresponding metadata, so the LVGL labels and cover
  can update together. The receiver copies validated glyphs to a separate
  PSRAM pool before displaying them. Older firmware without this
  characteristic keeps the previous text behavior.
- Playback payload: 12 bytes, little-endian `<BBBBII>`: version `1`, track ID,
  state (`0` unknown, `1` playing, `2` paused, `3` stopped), flags (bit 0 means
  timeline valid), elapsed milliseconds, and duration milliseconds. Missing
  timeline data sends flags `0` and both times `0`. Playback uses the same
  track ID as metadata and cover. A thirteenth byte, when present, carries the
  current Windows master volume percentage `0..100`.
- Control notification payload: version `1`, then opcode: `1` previous, `2`
  play/pause, `3` next (two bytes); `4` set Windows master volume plus one
  percentage byte `0..100` (three bytes); `5` seek the current track (seven
  bytes: version, opcode, track ID, and little-endian u32 elapsed milliseconds).
  The bridge checks the track ID and Chrome's current song, clamps the target to
  its timeline, and asks Windows GSMTC to change playback position. The bridge
  subscribes after connecting and applies transport commands to the selected
  Chrome GSMTC session.

## 不開終端機驗收

播放含 `telepatía` 的 `í`、中文字、日文或韓文的歌曲，確認歌名和歌手沒有缺字。背景記錄應出現 `Glyphs sent:`；這表示新版字形已送到 ESP32。

保持 Chrome 播放 YouTube Music，切換下一首，確認 TFT 歌名、封面和單色背景更新。點螢幕上的上一首、播放／暫停、下一首，確認 Windows 有反應，且右下角沒有按鍵狀態文字。拖動歌曲進度條並放開，確認 Chrome 跳轉到新位置；若 Windows/Chrome 不接受請求，`bridge.log` 會記錄 `Seek ... rejected`。點上方喇叭，拖動沒有外框或數字的短滑桿，確認 Windows 主音量改變；再從 Windows 調音量，確認滑桿位置同步。`%LOCALAPPDATA%\ESP32MusicDisplay\bridge.log` 會記錄 `JPEG cover sent:` 或 `Cover sent:` 的實測秒數，並標示快速傳輸或可靠回退；新版速度須以這台電腦的實測為準。

The three text fields are capped to 192, 128, and 128 UTF-8 bytes, so even
long metadata fits.

Primary references: [Microsoft GSMTC API](https://learn.microsoft.com/en-us/uwp/api/windows.media.control.globalsystemmediatransportcontrolssessionmanager),
[PyWinRT install guide](https://pywinrt.readthedocs.io/en/latest/installing/),
and [Bleak GATT client API](https://bleak.readthedocs.io/en/latest/api/client.html).
