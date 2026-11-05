#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

/* UTF-8 byte capacities, including the final NUL byte. */
#define MUSIC_BLE_TITLE_CAPACITY 193
#define MUSIC_BLE_ARTIST_CAPACITY 129
#define MUSIC_BLE_ALBUM_CAPACITY 129

typedef struct {
    char title[MUSIC_BLE_TITLE_CAPACITY];
    char artist[MUSIC_BLE_ARTIST_CAPACITY];
    char album[MUSIC_BLE_ALBUM_CAPACITY];
    uint8_t message_id;
} music_ble_metadata_t;

#define MUSIC_BLE_COVER_WIDTH 130
#define MUSIC_BLE_COVER_HEIGHT 130
#define MUSIC_BLE_COVER_BYTES (MUSIC_BLE_COVER_WIDTH * MUSIC_BLE_COVER_HEIGHT * 2)

typedef struct {
    /* RGB565 pixels in little-endian byte order. NULL and size 0 mean clear. */
    const uint8_t *pixels;
    size_t size;
    uint8_t image_id;
} music_ble_cover_t;

typedef struct {
    uint8_t track_id;
    uint8_t state; /* 0 unknown, 1 playing, 2 paused, 3 stopped */
    bool timeline_valid;
    uint32_t elapsed_ms;
    uint32_t duration_ms;
    bool volume_valid;
    uint8_t volume_percent;
} music_ble_playback_t;

/* ESP32 -> PC control notifications on the music service. Transport packets
 * are [version 1, command code]. Volume adds a percentage as byte 3; seek
 * adds track ID and a little-endian position in milliseconds. Keep command
 * codes aligned with the Windows bridge. */
typedef enum {
    MUSIC_BLE_CONTROL_PREVIOUS = 1,
    MUSIC_BLE_CONTROL_PLAY_PAUSE = 2,
    MUSIC_BLE_CONTROL_NEXT = 3,
    MUSIC_BLE_CONTROL_VOLUME = 4,
    MUSIC_BLE_CONTROL_SEEK = 5,
} music_ble_control_t;

/* Call once from app_main before the LVGL event loop. This starts advertising. */
esp_err_t music_ble_start(void);

/* Call only from the LVGL owner task, then update LVGL objects there. */
bool music_ble_try_get_update(music_ble_metadata_t *out);

/* Returns the newest completed cover. The returned PSRAM pointer stays valid
 * until another cover replaces it; never free or write through it. Call from
 * the LVGL owner task only. */
bool music_ble_try_get_cover(music_ble_cover_t *out);

/* Return the most recent playback/timeline update. Call from the LVGL owner
 * task; use track_id to discard updates for a different metadata track. */
bool music_ble_try_get_playback(music_ble_playback_t *out);

/* Called from the LVGL owner task when a transport button is clicked.
 * Returns true if a notification was accepted for an active subscribed PC.
 * This does not confirm that Windows completed the requested action. */
bool music_ble_send_control(music_ble_control_t command);

/* Set Windows master volume. A 3-byte notification carries the requested
 * percentage; playback updates report the actual volume from Windows. */
bool music_ble_send_volume(uint8_t percent);

/* Seek the current track to position_ms. The PC checks track_id against its
 * current media session before applying the request. The seven-byte packet
 * fits even the minimum BLE ATT MTU. */
bool music_ble_send_seek(uint8_t track_id, uint32_t position_ms);
