# 選用功能：將韌體燒入另一塊 ESP32-S3

`flash_firmware.cmd` 只適用於**相同硬體與接線**：ESP32-S3-WROOM-1-N16R8（16 MB Flash、8 MB PSRAM）、3.2 吋 ILI9341 240×320 SPI TFT（橫向顯示 320×240），以及 XPT2046 相容觸控。其他螢幕驅動、不同容量的 ESP32 或不同接線，需要重新編譯韌體。觸控座標校正也是依原螢幕實測值設定。

## 接線

接線前先拔除 ESP32 USB 電源。螢幕與觸控共用 3V3/GND。

| TFT 排針 | ESP32-S3 |
| --- | --- |
| VCC、LED | 3V3 |
| GND | GND |
| SCK | GPIO12 |
| SDI / MOSI | GPIO11 |
| SDO / MISO | GPIO13，可不接 |
| CS | GPIO10 |
| D/C | GPIO9 |
| RESET | GPIO8 |

| 觸控排針 | ESP32-S3 |
| --- | --- |
| T_CLK | GPIO4 |
| T_DIN | GPIO5 |
| T_OUT | GPIO6 |
| T_CS | GPIO14 |
| T_IRQ | GPIO21 |

## Windows 11 燒錄

1. 解壓**整個**發行 ZIP；確認 `flash_firmware.cmd`、`flash_firmware.py` 和 `firmware` 資料夾都在同一層。新電腦先雙擊 `setup_music_display.cmd` 完成背景橋接程式與 Python 3.14 安裝。
2. USB 接上自己的 ESP32-S3，關閉占用該 COM 埠的序列監視器。雙擊 `flash_firmware.cmd`。首次使用需有網路，這個可選工具會在此時才安裝固定版本的官方 `esptool`；日常背景連線不需使用它。
3. 視窗列出 COM 埠後，輸入 ESP32 對應的編號。工具先以唯讀方式確認 ESP32-S3 與 16 MB Flash；它無法從 COM 埠確認螢幕型號、實際接線或 8 MB PSRAM，這幾項需依上表自行核對。
4. 核對畫面上的板型、COM 埠和接線，再輸入要求的 `FLASH COMx` 確認詞。只有這一步之後才會覆寫該板原有韌體。取消時直接關閉視窗，或在確認處輸入其他內容。
5. 燒錄成功後開啟 Chrome YouTube Music。背景橋接程式會搜尋 `ESP32-Music-Display` 並自動連線；若尚未安裝背景橋接程式，請執行 `setup_music_display.cmd`。

如果停在 `Connecting`，先確認選的是 ESP32 的 COM 埠且沒有其他程式占用；部分開發板需要按住 `BOOT`、短按 `RESET`、再放開 `BOOT` 進入下載模式，然後重新執行燒錄工具。

燒錄包使用 ESP-IDF 的三個映像及原始位址：bootloader `0x0`、partition table `0x8000`、app `0x10000`；設定為 DIO、80 MHz、16 MB。工具先比對 ZIP 內映像的 SHA-256，再檢查映像標頭。`esptool` 在寫入後會驗證資料。這些韌體檔只會在打包最終編譯版本時產生；若資料夾缺檔，工具會停止。

若需要只檢查發行包、不連接板子，可在 PowerShell 進入解壓資料夾後執行 `py -3.14 .\flash_firmware.py --check-package`。

參考：[Espressif esptool 燒錄說明](https://docs.espressif.com/projects/esptool/en/latest/esp32s3/esptool/flashing-firmware.html)、[esptool 的晶片與 COM 埠選項](https://docs.espressif.com/projects/esptool/en/latest/esp32s3/esptool/basic-options.html)、[ESP-IDF 5.5.1 的 ESP32-S3 序列連接說明](https://docs.espressif.com/projects/esp-idf/en/v5.5.1/esp32s3/get-started/establish-serial-connection.html)。
