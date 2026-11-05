# ESP32-S3 YouTube Music display

This ESP-IDF project runs a 320×240 landscape LVGL interface on an ILI9341 SPI TFT. A Windows 11 helper reads Chrome/YouTube Music through Windows media sessions and sends the track title, artist, album, cover art, playback state, timeline, and Windows master volume over BLE. The Windows helper is in [`../music_pc_bridge`](../music_pc_bridge).

## 畫面與操作

畫面依照使用者提供的參考圖安排：左側是 130×130 專輯封面，右側顯示來源、歌名、歌手、播放進度與上一首／播放暫停／下一首按鍵。播放按鍵以 LVGL 畫出與預覽一致的白色三角形與直條；操作後不在畫面右下角顯示指令文字。進度條可拖動，拖動時稍微放大並預覽時間，放開後才向 Windows 要求跳轉。跳轉等待期間播放／暫停圖示仍依 Windows 回報更新，進度位置在 Windows 回報目標位置後立即接手更新。整片背景使用**單一深色**；收到新封面後，韌體從 RGB565 像素找主要色彩，再降低亮度，讓白色文字保持可讀。新封面傳輸期間保留上一張封面，完整收妥後才一起更新封面與背景色。沒有封面時使用預設深色。

右上角喇叭圖示會在頂部空白處顯示短滑桿，沒有外框或數字，也不會蓋住封面與歌曲資訊。拖動滑桿會透過 BLE 調整 **Windows 主音量**，也會接收電腦端的實際音量更新。這會影響 Windows 的其他應用程式。滑桿停止操作後會自動收起。按鍵控制仍綁在 LVGL 物件上，日後改動排版座標不需重寫藍牙控制邏輯。

## Wiring

| TFT pin | ESP32-S3 pin |
| --- | --- |
| VCC | 3V3 |
| GND | GND |
| LED | 3V3 (backlight) |
| SCK | GPIO12 |
| SDI (MOSI) | GPIO11 |
| SDO (MISO) | GPIO13, optional for this display |
| CS | GPIO10 |
| D/C | GPIO9 |
| RESET | GPIO8 |

The touch controller uses its own SPI3 bus, so the five touch wires do not
share the LCD's GPIO pins:

| Touch pin | ESP32-S3 pin |
| --- | --- |
| T_CLK | GPIO4 |
| T_DIN | GPIO5 |
| T_OUT | GPIO6 |
| T_CS | GPIO14 |
| T_IRQ | GPIO21 |

Disconnect USB power before changing wiring. The touch controller shares the
display module's existing 3V3 and GND supply.

## Current configuration

- ESP32-S3 CPU: 240 MHz. LVGL and SPI run on CPU1; NimBLE runs on CPU0.
- ILI9341 SPI: 40 MHz, tested on the replacement display.
- XPT2046-compatible touch SPI: independent SPI3 at 2 MHz, IRQ on GPIO21.
- LVGL 9.2: RGB565 partial rendering with two 20,480-byte internal DMA buffers.
- 130×130 RGB565 album art: PSRAM transfer and display buffers. The image changes only after a complete, validated BLE transfer arrives.
- BLE device name: `ESP32-Music-Display`.
- BLE requests a 7.5–15 ms connection interval, with a 15–30 ms fallback. The Windows PC decides the actual interval. The earlier write-with-response path measured 3.23 seconds for 33,800 bytes at 11.25 ms and MTU 247. After installing the new bridge, one live serial capture measured about 0.69 seconds from BLE metadata arrival to the complete 33,800-byte cover event; the user also confirmed visibly faster artwork. This is one observed end-to-end interval, not a guaranteed transfer rate.
- Fast cover transfer: version 2 chunks use GATT write without response; a final write with response carries CRC32. The image appears only if the expected byte count, offsets, and checksum match. The Windows helper retries with the version 1 write-with-response path if the fast attempt fails.

Build and flash with ESP-IDF 5.5.1:

```sh
idf.py build
idf.py -p /dev/ttyACM0 flash
```

## 手動驗收

1. 燒入韌體，在播放 YouTube Music 的 Windows 11 電腦安裝同版背景橋接套件。既有背景版可直接用新版 `install_background.cmd` 升級。
2. 播放歌曲並切換下一首；確認歌名先更新，封面與單色背景隨新封面更新，且進度與時間繼續變動。
3. 點上一首、播放／暫停、下一首，確認電腦真的執行，且右下角不出現指令文字。拖動歌曲進度條至另一位置後放開，確認 Chrome 跳轉並回報新時間。點上方喇叭，拖動滑桿，確認 Windows 音量顯示跟著變動；也從 Windows 調整音量，確認滑桿回報正確。
4. 若畫面顯示 `PC OFFLINE` 或歌曲未切換，查看電腦上的 `%LOCALAPPDATA%\ESP32MusicDisplay\bridge.log`。背景連線不需要保持終端機視窗開啟。

觸控 X/Y 校正使用這塊螢幕的實測四角資料；若更換觸控面板或旋轉方向，需要重新校正。預設 LVGL 字型尚未完整涵蓋中日韓文字；若要廣泛顯示 CJK，需另外加入字型並檢查 app 分割區容量。
