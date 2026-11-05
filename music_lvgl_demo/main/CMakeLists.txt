idf_component_register(SRCS "main.c" "music_ui.c" "touch_xpt2046.c"
                       INCLUDE_DIRS "."
                       REQUIRES esp_lcd esp_driver_spi esp_driver_gpio esp_timer esp_psram music_ble)
