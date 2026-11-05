ESP32 音樂顯示器：只安裝 Windows 背景程式

適用：ESP32-S3 已燒入目前的 LVGL／BLE 字型版韌體；電腦為 Windows 11 64 位元，並用 Chrome 播放 YouTube Music。

1. 完整解壓縮這個 ZIP，不要在壓縮檔視窗內直接執行。
2. 進入 music_pc_bridge 資料夾，雙擊 setup_music_display.cmd。
3. 看到「已安裝背景連線」後，開啟 Windows 藍牙並讓 ESP32 通電；在 Chrome 播放 YouTube Music。程式會在背景連線，平常不需開終端機。

安裝程式會在缺少相容 Python 時，從 Python 官方網站下載、驗證並安裝 Python 3.14.4，接著安裝必要套件。首次安裝需有網路，無需管理員權限。已安裝舊版時可再次雙擊同一檔案升級。

這份 ZIP 只有電腦端程式，沒有 ESP32 韌體或燒錄工具。若螢幕尚未燒入匹配的韌體，單裝此 ZIP 無法讓畫面顯示。

支援：歌名／歌手／專輯、封面、背景色、播放進度，以及螢幕的上一首／播放暫停／下一首、進度拖動和 Windows 主音量。中文、日文、韓文與 telepatía 的 í 已在同版韌體實機測試。字型採 Noto Sans CJK，授權見 fonts/OFL.txt；不保證涵蓋所有 emoji 或罕見字。

停用背景連線：雙擊 disable_background.cmd。
診斷時暫時顯示終端機：雙擊 start_music_display.cmd，關閉後背景版會恢復。
背景記錄：%LOCALAPPDATA%\ESP32MusicDisplay\bridge.log
