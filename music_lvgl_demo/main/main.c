#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "driver/spi_master.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_lcd_ili9341.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_log.h"
#include "esp_psram.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lvgl.h"
#include "music_ble.h"
#include "music_ui.h"
#include "touch_xpt2046.h"

#define LCD_HOST SPI2_HOST
#define LCD_HRES 320
#define LCD_VRES 240
#define DRAW_LINES 32
#define PIN_LCD_SCLK 12
#define PIN_LCD_MOSI 11
#define PIN_LCD_MISO 13
#define PIN_LCD_CS 10
#define PIN_LCD_DC 9
#define PIN_LCD_RESET 8
#define METADATA_WAIT_FOR_PLAYBACK_MS 250
#define METADATA_COVER_FALLBACK_MS 4500

static const char *TAG = "music_demo";

typedef struct {
    music_ui_t *ui;
    lv_point_t last_point;
    uint32_t last_command_tick;
    uint32_t last_volume_send_tick;
    uint32_t volume_hold_until;
    uint32_t volume_hide_deadline;
    uint8_t pending_volume;
    bool volume_pending;
    bool volume_dragging;
    bool volume_visible;
    bool applying_volume;
    uint32_t current_duration_ms;
    uint32_t seek_target_ms;
    uint32_t seek_hold_until;
    uint8_t current_track_id;
    uint8_t drag_track_id;
    bool timeline_valid;
    bool progress_dragging;
    bool progress_pending;
} touch_context_t;

typedef struct {
    music_ble_metadata_t metadata;
    music_ble_playback_t playback;
    uint32_t started_tick;
    bool active;
    bool cover_ready;
    bool cover_clear;
    bool playback_ready;
} pending_track_t;

// The controller's Y ADC runs left-to-right and its X ADC runs top-to-bottom
// on this landscape module. The measured 5 mm inset points were:
// top-right raw (480, 3630) -> pixel (300, 20),
// bottom-left raw (3590, 420) -> pixel (20, 220).
static int16_t map_raw(uint16_t value, int32_t raw_start, int32_t raw_end,
                       int32_t pixel_start, int32_t pixel_end, int32_t limit)
{
    int32_t pixel = pixel_start +
                    ((int32_t)value - raw_start) * (pixel_end - pixel_start) /
                        (raw_end - raw_start);
    if (pixel < 0) pixel = 0;
    if (pixel >= limit) pixel = limit - 1;
    return (int16_t)pixel;
}

static void touch_read_cb(lv_indev_t *indev, lv_indev_data_t *data)
{
    touch_context_t *ctx = lv_indev_get_user_data(indev);
    uint16_t raw_x = 0;
    uint16_t raw_y = 0;
    const bool pressed = touch_xpt2046_read_raw(&raw_x, &raw_y);
    data->state = pressed ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
    if (pressed) {
        ctx->last_point.x = map_raw(raw_y, 420, 3630, 20, 300, LCD_HRES);
        ctx->last_point.y = map_raw(raw_x, 480, 3590, 20, 220, LCD_VRES);
    }
    data->point = ctx->last_point;
}

static void button_control_cb(lv_event_t *event)
{
    touch_context_t *ctx = lv_event_get_user_data(event);
    lv_obj_t *button = lv_event_get_target_obj(event);
    music_ble_control_t command;
    const char *button_name;
    if (button == ctx->ui->previous_button) {
        command = MUSIC_BLE_CONTROL_PREVIOUS;
        button_name = "PREVIOUS";
    } else if (button == ctx->ui->play_button) {
        command = MUSIC_BLE_CONTROL_PLAY_PAUSE;
        button_name = "PLAY/PAUSE";
    } else if (button == ctx->ui->next_button) {
        command = MUSIC_BLE_CONTROL_NEXT;
        button_name = "NEXT";
    } else {
        return;
    }

    // Ignore a repeated click caused by brief contact bounce on the resistive
    // touch panel. The button objects remain the same if the layout moves.
    uint32_t now = lv_tick_get();
    if (ctx->last_command_tick != 0 && now - ctx->last_command_tick < 350) {
        return;
    }
    ctx->last_command_tick = now;

    bool sent = music_ble_send_control(command);
    ESP_LOGI(TAG, "%s %s", button_name, sent ? "queued" : "not connected");
}

static void send_volume(touch_context_t *ctx, uint8_t percent)
{
    const bool sent = music_ble_send_volume(percent);
    ctx->last_volume_send_tick = lv_tick_get();
    ctx->volume_hold_until = ctx->last_volume_send_tick + 1000;
    ESP_LOGI(TAG, "volume request: %u%% (%s)", (unsigned)percent,
             sent ? "queued" : "not connected");
}

static void volume_button_cb(lv_event_t *event)
{
    touch_context_t *ctx = lv_event_get_user_data(event);
    ctx->volume_visible = !ctx->volume_visible;
    music_ui_show_volume(ctx->ui, ctx->volume_visible);
    ctx->volume_hide_deadline = ctx->volume_visible ? lv_tick_get() + 5000 : 0;
}

static void volume_event_cb(lv_event_t *event)
{
    touch_context_t *ctx = lv_event_get_user_data(event);
    if (ctx->applying_volume) return;

    lv_event_code_t code = lv_event_get_code(event);
    if (code == LV_EVENT_PRESSED) {
        ctx->volume_dragging = true;
        ctx->volume_hide_deadline = 0;
    } else if (code == LV_EVENT_VALUE_CHANGED) {
        int32_t value = lv_slider_get_value(ctx->ui->volume);
        if (value < 0) value = 0;
        if (value > 100) value = 100;
        ctx->applying_volume = true;
        music_ui_set_volume(ctx->ui, (uint8_t)value);
        ctx->applying_volume = false;
        ctx->pending_volume = (uint8_t)value;
        ctx->volume_pending = true;
        uint32_t now = lv_tick_get();
        if (ctx->last_volume_send_tick == 0 ||
            now - ctx->last_volume_send_tick >= 120) {
            send_volume(ctx, ctx->pending_volume);
            ctx->volume_pending = false;
        }
    } else if (code == LV_EVENT_RELEASED) {
        ctx->volume_dragging = false;
        if (ctx->volume_pending) {
            send_volume(ctx, ctx->pending_volume);
            ctx->volume_pending = false;
        }
        ctx->volume_hide_deadline = lv_tick_get() + 3000;
    }
}

static void progress_event_cb(lv_event_t *event)
{
    touch_context_t *ctx = lv_event_get_user_data(event);
    const lv_event_code_t code = lv_event_get_code(event);
    if (code == LV_EVENT_PRESSED) {
        ctx->progress_dragging = true;
        ctx->progress_pending = false;
        ctx->drag_track_id = ctx->current_track_id;
    } else if (code == LV_EVENT_VALUE_CHANGED) {
        if (!ctx->progress_dragging || !ctx->timeline_valid ||
            ctx->current_duration_ms == 0) {
            return;
        }
        int32_t value = lv_slider_get_value(ctx->ui->progress);
        if (value < 0) value = 0;
        if (value > 1000) value = 1000;
        ctx->seek_target_ms = (uint32_t)((uint64_t)value *
                              ctx->current_duration_ms / 1000u);
        ctx->progress_pending = true;
        music_ui_preview_seek(ctx->ui, ctx->seek_target_ms,
                              ctx->current_duration_ms);
    } else if (code == LV_EVENT_RELEASED) {
        ctx->progress_dragging = false;
        if (ctx->progress_pending && ctx->timeline_valid &&
            ctx->drag_track_id == ctx->current_track_id) {
            const bool sent = music_ble_send_seek(ctx->current_track_id,
                                                  ctx->seek_target_ms);
            ctx->seek_hold_until = sent ? lv_tick_get() + 1200 : 0;
            ESP_LOGI(TAG, "seek to %u ms, track=%u (%s)",
                     (unsigned)ctx->seek_target_ms,
                     (unsigned)ctx->current_track_id,
                     sent ? "queued" : "not connected");
        }
        ctx->progress_pending = false;
    }
}

static void apply_visible_playback(music_ui_t *ui, touch_context_t *ctx,
                                   const music_ble_playback_t *playback)
{
    ctx->timeline_valid = playback->timeline_valid;
    ctx->current_duration_ms = playback->duration_ms;
    // The button state remains live while the timeline waits for a seek.
    music_ui_set_play_state(ui, playback->state);
    if (ctx->seek_hold_until != 0 && playback->timeline_valid) {
        const uint32_t delta = playback->elapsed_ms > ctx->seek_target_ms
                                   ? playback->elapsed_ms - ctx->seek_target_ms
                                   : ctx->seek_target_ms - playback->elapsed_ms;
        if (delta <= 1500) ctx->seek_hold_until = 0;
    }
    if (!ctx->progress_dragging &&
        (int32_t)(lv_tick_get() - ctx->seek_hold_until) >= 0) {
        music_ui_set_playback(ui, playback->state, playback->timeline_valid,
                              playback->elapsed_ms, playback->duration_ms);
    }
    if (playback->volume_valid && !ctx->volume_dragging &&
        (int32_t)(lv_tick_get() - ctx->volume_hold_until) >= 0) {
        ctx->applying_volume = true;
        music_ui_set_volume(ui, playback->volume_percent);
        ctx->applying_volume = false;
    }
}

static bool on_color_done(esp_lcd_panel_io_handle_t io,
                          esp_lcd_panel_io_event_data_t *event,
                          void *user_ctx)
{
    (void)io;
    (void)event;
    lv_display_flush_ready((lv_display_t *)user_ctx);
    return false;
}

static void flush_cb(lv_display_t *display, const lv_area_t *area, uint8_t *pixels)
{
    esp_lcd_panel_handle_t panel = lv_display_get_user_data(display);
    const uint32_t count = (uint32_t)(area->x2 - area->x1 + 1) *
                           (uint32_t)(area->y2 - area->y1 + 1);
    lv_draw_sw_rgb565_swap(pixels, count);
    ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(panel, area->x1, area->y1,
                                               area->x2 + 1, area->y2 + 1, pixels));
}

static void lvgl_tick(void *arg)
{
    (void)arg;
    lv_tick_inc(2);
}

void app_main(void)
{
    ESP_LOGI(TAG, "PSRAM: %s, free=%u bytes", esp_psram_is_initialized() ? "ready" : "missing",
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));

    spi_bus_config_t bus_cfg = {
        .sclk_io_num = PIN_LCD_SCLK,
        .mosi_io_num = PIN_LCD_MOSI,
        .miso_io_num = PIN_LCD_MISO,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = LCD_HRES * DRAW_LINES * 2,
    };
    ESP_ERROR_CHECK(spi_bus_initialize(LCD_HOST, &bus_cfg, SPI_DMA_CH_AUTO));

    esp_lcd_panel_io_spi_config_t io_cfg = {
        .dc_gpio_num = PIN_LCD_DC,
        .cs_gpio_num = PIN_LCD_CS,
        .pclk_hz = 40 * 1000 * 1000,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
        .spi_mode = 0,
        .trans_queue_depth = 4,
    };
    esp_lcd_panel_io_handle_t io = NULL;
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)LCD_HOST,
                                             &io_cfg, &io));

    esp_lcd_panel_dev_config_t panel_cfg = {
        .reset_gpio_num = PIN_LCD_RESET,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_BGR,
        .bits_per_pixel = 16,
    };
    esp_lcd_panel_handle_t panel = NULL;
    ESP_ERROR_CHECK(esp_lcd_new_panel_ili9341(io, &panel_cfg, &panel));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_swap_xy(panel, true));
    ESP_ERROR_CHECK(esp_lcd_panel_mirror(panel, true, true));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel, true));

    lv_init();
    lv_display_t *display = lv_display_create(LCD_HRES, LCD_VRES);
    ESP_ERROR_CHECK(display ? ESP_OK : ESP_ERR_NO_MEM);
    const size_t buffer_bytes = LCD_HRES * DRAW_LINES * sizeof(lv_color16_t);
    void *buf_a = heap_caps_malloc(buffer_bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
    void *buf_b = heap_caps_malloc(buffer_bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
    ESP_ERROR_CHECK((buf_a && buf_b) ? ESP_OK : ESP_ERR_NO_MEM);
    lv_display_set_color_format(display, LV_COLOR_FORMAT_RGB565);
    lv_display_set_buffers(display, buf_a, buf_b, buffer_bytes, LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_user_data(display, panel);
    lv_display_set_flush_cb(display, flush_cb);

    const esp_lcd_panel_io_callbacks_t cbs = {.on_color_trans_done = on_color_done};
    ESP_ERROR_CHECK(esp_lcd_panel_io_register_event_callbacks(io, &cbs, display));

    const esp_timer_create_args_t tick_cfg = {.callback = lvgl_tick, .name = "lvgl_tick"};
    esp_timer_handle_t tick = NULL;
    ESP_ERROR_CHECK(esp_timer_create(&tick_cfg, &tick));
    ESP_ERROR_CHECK(esp_timer_start_periodic(tick, 2000));

    static music_ui_t ui;
    music_ui_create(lv_screen_active(), &ui);

    // The BLE receiver may reuse either of its transfer buffers on a rapid
    // series of skips. Keep display-owned art stable until LVGL switches to a
    // completed new image; this also avoids an empty cover while loading.
    uint8_t *display_art[2] = {
        heap_caps_malloc(MUSIC_BLE_COVER_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT),
        heap_caps_malloc(MUSIC_BLE_COVER_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT),
    };
    ESP_ERROR_CHECK((display_art[0] && display_art[1]) ? ESP_OK : ESP_ERR_NO_MEM);
    uint8_t next_display_art = 0;

    ESP_ERROR_CHECK(touch_xpt2046_init());
    static touch_context_t touch_ctx;
    touch_ctx.ui = &ui;
    lv_indev_t *touch_indev = lv_indev_create();
    ESP_ERROR_CHECK(touch_indev ? ESP_OK : ESP_ERR_NO_MEM);
    lv_indev_set_type(touch_indev, LV_INDEV_TYPE_POINTER);
    lv_indev_set_display(touch_indev, display);
    lv_indev_set_user_data(touch_indev, &touch_ctx);
    lv_indev_set_read_cb(touch_indev, touch_read_cb);
    lv_timer_set_period(lv_indev_get_read_timer(touch_indev), 30);
    lv_obj_add_event_cb(ui.previous_button, button_control_cb, LV_EVENT_CLICKED, &touch_ctx);
    lv_obj_add_event_cb(ui.play_button, button_control_cb, LV_EVENT_CLICKED, &touch_ctx);
    lv_obj_add_event_cb(ui.next_button, button_control_cb, LV_EVENT_CLICKED, &touch_ctx);
    lv_obj_add_event_cb(ui.volume_button, volume_button_cb, LV_EVENT_CLICKED, &touch_ctx);
    lv_obj_add_event_cb(ui.volume, volume_event_cb, LV_EVENT_PRESSED, &touch_ctx);
    lv_obj_add_event_cb(ui.volume, volume_event_cb, LV_EVENT_VALUE_CHANGED, &touch_ctx);
    lv_obj_add_event_cb(ui.volume, volume_event_cb, LV_EVENT_RELEASED, &touch_ctx);
    lv_obj_add_event_cb(ui.progress, progress_event_cb, LV_EVENT_PRESSED, &touch_ctx);
    lv_obj_add_event_cb(ui.progress, progress_event_cb, LV_EVENT_VALUE_CHANGED, &touch_ctx);
    lv_obj_add_event_cb(ui.progress, progress_event_cb, LV_EVENT_RELEASED, &touch_ctx);

    ESP_ERROR_CHECK(music_ble_start());
    ESP_LOGI(TAG, "LVGL 320x240 ready; SPI=40 MHz; DMA buffers=%u bytes each",
             (unsigned)buffer_bytes);
    ESP_LOGI(TAG, "Internal free=%u, minimum=%u, largest block=%u",
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));

    uint8_t current_metadata_id = 0;
    bool have_metadata = false;
    while (true) {
        music_ble_metadata_t update;
        while (music_ble_try_get_update(&update)) {
            // Keep the previous art visible until the new art has arrived.
            music_ui_set_playback(&ui, 0, false, 0, 0);
            current_metadata_id = update.message_id;
            have_metadata = true;
            touch_ctx.current_track_id = update.message_id;
            touch_ctx.current_duration_ms = 0;
            touch_ctx.timeline_valid = false;
            touch_ctx.progress_pending = false;
            touch_ctx.seek_hold_until = 0;
            lv_label_set_text(ui.title, update.title[0] ? update.title : "No music");
            lv_label_set_text(ui.artist, update.artist);
            lv_label_set_text(ui.source, update.album[0] ? update.album : "YOUTUBE MUSIC");
            ESP_LOGI(TAG, "BLE metadata: %s / %s / %s",
                     update.title, update.artist, update.album);
        }
        music_ble_cover_t cover;
        while (music_ble_try_get_cover(&cover)) {
            if (!have_metadata || cover.image_id != current_metadata_id) continue;
            bool updated = false;
            if (cover.pixels == NULL && cover.size == 0) {
                updated = music_ui_set_cover(&ui, NULL, 0);
            } else if (cover.pixels != NULL && cover.size == MUSIC_BLE_COVER_BYTES) {
                memcpy(display_art[next_display_art], cover.pixels, cover.size);
                updated = music_ui_set_cover(&ui, display_art[next_display_art], cover.size);
                if (updated) next_display_art ^= 1u;
            }
            if (updated) {
                ESP_LOGI(TAG, "BLE cover: %u bytes, id=%u",
                         (unsigned)cover.size, (unsigned)cover.image_id);
            }
        }
        music_ble_playback_t playback;
        while (music_ble_try_get_playback(&playback)) {
            if (have_metadata && playback.track_id == current_metadata_id) {
                touch_ctx.timeline_valid = playback.timeline_valid;
                touch_ctx.current_duration_ms = playback.duration_ms;
                // Keep the play/pause icon current even while the position
                // slider waits for Windows to apply a seek. The old blanket
                // hold made the button appear stuck after dragging.
                music_ui_set_play_state(&ui, playback.state);
                if (touch_ctx.seek_hold_until != 0 && playback.timeline_valid) {
                    const uint32_t delta = playback.elapsed_ms > touch_ctx.seek_target_ms
                                               ? playback.elapsed_ms - touch_ctx.seek_target_ms
                                               : touch_ctx.seek_target_ms - playback.elapsed_ms;
                    if (delta <= 1500) touch_ctx.seek_hold_until = 0;
                }
                if (!touch_ctx.progress_dragging &&
                    (int32_t)(lv_tick_get() - touch_ctx.seek_hold_until) >= 0) {
                    music_ui_set_playback(&ui, playback.state, playback.timeline_valid,
                                          playback.elapsed_ms, playback.duration_ms);
                }
                if (playback.volume_valid && !touch_ctx.volume_dragging &&
                    (int32_t)(lv_tick_get() - touch_ctx.volume_hold_until) >= 0) {
                    touch_ctx.applying_volume = true;
                    music_ui_set_volume(&ui, playback.volume_percent);
                    touch_ctx.applying_volume = false;
                }
            }
        }
        if (touch_ctx.volume_visible && !touch_ctx.volume_dragging &&
            touch_ctx.volume_hide_deadline != 0 &&
            (int32_t)(lv_tick_get() - touch_ctx.volume_hide_deadline) >= 0) {
            music_ui_show_volume(&ui, false);
            touch_ctx.volume_visible = false;
            touch_ctx.volume_hide_deadline = 0;
        }
        uint32_t delay_ms = lv_timer_handler();
        if (delay_ms < 5) delay_ms = 5;
        if (delay_ms > 20) delay_ms = 20;
        TickType_t ticks = pdMS_TO_TICKS(delay_ms);
        vTaskDelay(ticks > 0 ? ticks : 1);
    }
}
