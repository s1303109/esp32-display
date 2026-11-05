#include "music_ui.h"
#include "src/draw/lv_draw_triangle.h"

#include <stdio.h>
#include <string.h>

#define COVER_SIZE 130
#define COVER_BYTES (COVER_SIZE * COVER_SIZE * 2)

typedef struct {
    uint8_t r;
    uint8_t g;
    uint8_t b;
} rgb8_t;

static rgb8_t cover_pixel(const uint8_t *pixels, int x, int y)
{
    const size_t offset = ((size_t)y * COVER_SIZE + (size_t)x) * 2;
    const uint16_t rgb565 = (uint16_t)pixels[offset] |
                            ((uint16_t)pixels[offset + 1] << 8);
    return (rgb8_t){
        .r = (uint8_t)((((rgb565 >> 11) & 0x1f) * 255 + 15) / 31),
        .g = (uint8_t)((((rgb565 >> 5) & 0x3f) * 255 + 31) / 63),
        .b = (uint8_t)(((rgb565 & 0x1f) * 255 + 15) / 31),
    };
}

static uint16_t color_bucket(rgb8_t color)
{
    return (uint16_t)(((uint16_t)(color.r >> 5) << 6) |
                      ((uint16_t)(color.g >> 5) << 3) |
                      (color.b >> 5));
}

static bool colored_midtone(rgb8_t color)
{
    uint8_t largest = color.r > color.g ? color.r : color.g;
    if (color.b > largest) largest = color.b;
    uint8_t smallest = color.r < color.g ? color.r : color.g;
    if (color.b < smallest) smallest = color.b;
    const unsigned brightness = (54u * color.r + 183u * color.g +
                                 19u * color.b) >> 8;
    return brightness > 28 && brightness < 230 &&
           (unsigned)(largest - smallest) >= 22;
}

static rgb8_t dominant_cover_color(const uint8_t *pixels)
{
    // Quantized histogram is only 1 KiB on the LVGL task stack. Sampling
    // every other pixel keeps the 130x130 artwork analysis inexpensive.
    uint16_t bins[512] = {0};
    uint16_t best_bucket = 0;
    uint16_t best_count = 0;
    bool use_midtone_filter = true;

    for (int pass = 0; pass < 2; pass++) {
        memset(bins, 0, sizeof(bins));
        best_count = 0;
        for (int y = 0; y < COVER_SIZE; y += 2) {
            for (int x = 0; x < COVER_SIZE; x += 2) {
                rgb8_t color = cover_pixel(pixels, x, y);
                if (use_midtone_filter && !colored_midtone(color)) continue;
                const uint16_t bucket = color_bucket(color);
                const uint16_t count = ++bins[bucket];
                if (count > best_count) {
                    best_count = count;
                    best_bucket = bucket;
                }
            }
        }
        if (best_count >= 20 || !use_midtone_filter) break;
        use_midtone_filter = false;
    }

    if (best_count == 0) return (rgb8_t){96, 90, 110};
    uint32_t total_r = 0;
    uint32_t total_g = 0;
    uint32_t total_b = 0;
    uint32_t count = 0;
    for (int y = 0; y < COVER_SIZE; y += 2) {
        for (int x = 0; x < COVER_SIZE; x += 2) {
            rgb8_t color = cover_pixel(pixels, x, y);
            if (color_bucket(color) != best_bucket) continue;
            if (use_midtone_filter && !colored_midtone(color)) continue;
            total_r += color.r;
            total_g += color.g;
            total_b += color.b;
            count++;
        }
    }
    if (count == 0) return (rgb8_t){96, 90, 110};
    return (rgb8_t){
        (uint8_t)(total_r / count),
        (uint8_t)(total_g / count),
        (uint8_t)(total_b / count),
    };
}

static uint8_t mix_channel(uint8_t base, uint8_t foreground,
                           unsigned foreground_percent)
{
    return (uint8_t)(((unsigned)base * (100 - foreground_percent) +
                      (unsigned)foreground * foreground_percent + 50) / 100);
}

static rgb8_t mix_color(rgb8_t base, rgb8_t foreground,
                        unsigned foreground_percent)
{
    return (rgb8_t){
        mix_channel(base.r, foreground.r, foreground_percent),
        mix_channel(base.g, foreground.g, foreground_percent),
        mix_channel(base.b, foreground.b, foreground_percent),
    };
}

static uint32_t rgb_hex(rgb8_t color)
{
    return ((uint32_t)color.r << 16) |
           ((uint32_t)color.g << 8) | color.b;
}

static rgb8_t cap_background_brightness(rgb8_t color)
{
    // Bright artwork still needs a dark, readable full-screen background.
    if (color.r > 100) color.r = 100;
    if (color.g > 100) color.g = 100;
    if (color.b > 100) color.b = 100;
    return color;
}

static void apply_cover_theme(music_ui_t *ui, const uint8_t *pixels)
{
    if (ui == NULL || ui->screen == NULL) return;
    const rgb8_t dominant = pixels ? dominant_cover_color(pixels) :
                                     (rgb8_t){52, 70, 82};
    const rgb8_t base = cap_background_brightness(
        mix_color((rgb8_t){9, 12, 20}, dominant, 44));
    const rgb8_t panel = mix_color(base, (rgb8_t){0, 0, 0}, 20);
    const rgb8_t track = mix_color(base, (rgb8_t){255, 255, 255}, 22);

    // Screen and its full-size content layer use exactly one flat album hue.
    lv_obj_set_style_bg_color(ui->screen, lv_color_hex(rgb_hex(base)), 0);
    lv_obj_set_style_bg_color(ui->card, lv_color_hex(rgb_hex(base)), 0);
    lv_obj_set_style_bg_color(ui->cover, lv_color_hex(rgb_hex(panel)), 0);
    lv_obj_set_style_bg_color(ui->progress,
                              lv_color_hex(rgb_hex(track)), LV_PART_MAIN);
    lv_obj_set_style_bg_color(ui->progress,
                              lv_color_hex(0xFFFFFF), LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(ui->volume,
                              lv_color_hex(rgb_hex(track)), LV_PART_MAIN);
    lv_obj_set_style_bg_color(ui->volume,
                              lv_color_hex(0xFFFFFF), LV_PART_INDICATOR);
}

static void remove_padding(lv_obj_t *obj)
{
    lv_obj_set_style_pad_top(obj, 0, 0);
    lv_obj_set_style_pad_bottom(obj, 0, 0);
    lv_obj_set_style_pad_left(obj, 0, 0);
    lv_obj_set_style_pad_right(obj, 0, 0);
}

static lv_obj_t *make_label(lv_obj_t *parent, const char *text,
                            int x, int y, int width, uint32_t color)
{
    lv_obj_t *label = lv_label_create(parent);
    lv_label_set_text(label, text);
    lv_label_set_long_mode(label, LV_LABEL_LONG_DOT);
    lv_obj_set_pos(label, x, y);
    lv_obj_set_size(label, width, 18);
    lv_obj_set_style_text_color(label, lv_color_hex(color), 0);
    return label;
}

static lv_obj_t *make_round_button(lv_obj_t *parent, const char *symbol,
                                    int x, int y, int size,
                                    uint32_t background, uint32_t foreground)
{
    lv_obj_t *button = lv_button_create(parent);
    lv_obj_set_pos(button, x, y);
    lv_obj_set_size(button, size, size);
    lv_obj_set_style_radius(button, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(button, lv_color_hex(background), 0);
    lv_obj_set_style_bg_opa(button, LV_OPA_TRANSP, 0);
    lv_obj_set_style_bg_color(button, lv_color_hex(0xFFFFFF), LV_STATE_PRESSED);
    lv_obj_set_style_bg_opa(button, LV_OPA_20, LV_STATE_PRESSED);
    lv_obj_set_style_border_width(button, 0, 0);
    lv_obj_set_style_shadow_width(button, 0, 0);
    remove_padding(button);
    lv_obj_remove_flag(button, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *icon = lv_label_create(button);
    lv_label_set_text(icon, symbol);
    lv_obj_set_style_text_color(icon, lv_color_hex(foreground), 0);
    lv_obj_set_style_text_font(icon, &lv_font_montserrat_20, 0);
    lv_obj_set_style_transform_scale(icon, 325, 0);
    lv_obj_center(icon);
    return button;
}

typedef enum {
    TRANSPORT_PREVIOUS,
    TRANSPORT_PLAY_PAUSE,
    TRANSPORT_NEXT,
} transport_icon_kind_t;

static const transport_icon_kind_t previous_icon_kind = TRANSPORT_PREVIOUS;
static const transport_icon_kind_t play_pause_icon_kind = TRANSPORT_PLAY_PAUSE;
static const transport_icon_kind_t next_icon_kind = TRANSPORT_NEXT;

static void draw_white_triangle(lv_layer_t *layer, int32_t x0, int32_t y0,
                                int32_t x1, int32_t y1, int32_t x2, int32_t y2)
{
    lv_draw_triangle_dsc_t dsc;
    lv_draw_triangle_dsc_init(&dsc);
    dsc.bg_color = lv_color_white();
    dsc.p[0].x = x0;
    dsc.p[0].y = y0;
    dsc.p[1].x = x1;
    dsc.p[1].y = y1;
    dsc.p[2].x = x2;
    dsc.p[2].y = y2;
    lv_draw_triangle(layer, &dsc);
}

static void draw_white_bar(lv_layer_t *layer, int32_t x0, int32_t y0,
                           int32_t x1, int32_t y1)
{
    lv_draw_rect_dsc_t dsc;
    lv_draw_rect_dsc_init(&dsc);
    dsc.bg_color = lv_color_white();
    dsc.radius = 1;
    const lv_area_t area = {.x1 = x0, .y1 = y0, .x2 = x1, .y2 = y1};
    lv_draw_rect(layer, &dsc, &area);
}

static void draw_transport_icon_cb(lv_event_t *event)
{
    if (lv_event_get_code(event) != LV_EVENT_DRAW_MAIN) return;

    lv_obj_t *icon = lv_event_get_target(event);
    const transport_icon_kind_t kind =
        *(const transport_icon_kind_t *)lv_event_get_user_data(event);
    lv_layer_t *layer = lv_event_get_layer(event);
    lv_area_t area;
    lv_obj_get_coords(icon, &area);
    const int32_t x = area.x1;
    const int32_t y = area.y1;

    if (kind == TRANSPORT_PREVIOUS) {
        // Match the approved preview's two overlapping left-pointing tips.
        draw_white_triangle(layer, x + 14, y + 20, x + 27, y + 13, x + 27, y + 27);
        draw_white_triangle(layer, x + 22, y + 20, x + 35, y + 13, x + 35, y + 27);
    } else if (kind == TRANSPORT_NEXT) {
        draw_white_triangle(layer, x + 11, y + 13, x + 11, y + 27, x + 24, y + 20);
        draw_white_triangle(layer, x + 19, y + 13, x + 19, y + 27, x + 32, y + 20);
    } else if (lv_obj_has_state(icon, LV_STATE_USER_1)) {
        draw_white_bar(layer, x + 12, y + 15, x + 17, y + 37);
        draw_white_bar(layer, x + 23, y + 15, x + 28, y + 37);
    } else {
        draw_white_triangle(layer, x + 12, y + 15, x + 31, y + 26, x + 12, y + 37);
    }
}

static lv_obj_t *make_transport_button(lv_obj_t *parent, int x, int y,
                                        int size,
                                        const transport_icon_kind_t *kind,
                                        lv_obj_t **icon_out)
{
    lv_obj_t *button = lv_button_create(parent);
    lv_obj_set_pos(button, x, y);
    lv_obj_set_size(button, size, size);
    lv_obj_set_style_radius(button, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(button, LV_OPA_TRANSP, 0);
    lv_obj_set_style_bg_color(button, lv_color_white(), LV_STATE_PRESSED);
    lv_obj_set_style_bg_opa(button, LV_OPA_20, LV_STATE_PRESSED);
    lv_obj_set_style_border_width(button, 0, 0);
    lv_obj_set_style_shadow_width(button, 0, 0);
    remove_padding(button);
    lv_obj_remove_flag(button, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *icon = lv_obj_create(button);
    lv_obj_set_size(icon, size, size);
    lv_obj_center(icon);
    lv_obj_set_style_bg_opa(icon, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(icon, 0, 0);
    lv_obj_set_style_shadow_width(icon, 0, 0);
    remove_padding(icon);
    lv_obj_remove_flag(icon, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(icon, draw_transport_icon_cb, LV_EVENT_DRAW_MAIN,
                        (void *)kind);
    if (icon_out != NULL) *icon_out = icon;
    return button;
}

void music_ui_create(lv_obj_t *screen, music_ui_t *ui)
{
    memset(ui, 0, sizeof(*ui));
    ui->screen = screen;
    lv_obj_clean(screen);
    lv_obj_set_style_bg_color(screen, lv_color_hex(0x1C2630), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(screen, 0, 0);
    remove_padding(screen);
    lv_obj_remove_flag(screen, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *card = lv_obj_create(screen);
    ui->card = card;
    lv_obj_set_pos(card, 0, 0);
    lv_obj_set_size(card, 320, 240);
    lv_obj_set_style_radius(card, 0, 0);
    lv_obj_set_style_bg_color(card, lv_color_hex(0x1C2630), 0);
    lv_obj_set_style_border_width(card, 0, 0);
    lv_obj_set_style_shadow_width(card, 0, 0);
    remove_padding(card);
    lv_obj_remove_flag(card, LV_OBJ_FLAG_SCROLLABLE);

    // Placeholder art. A track thumbnail can replace these cover children later.
    lv_obj_t *cover = lv_obj_create(card);
    ui->cover = cover;
    lv_obj_set_pos(cover, 14, 54);
    lv_obj_set_size(cover, 130, 130);
    lv_obj_set_style_radius(cover, 12, 0);
    lv_obj_set_style_bg_color(cover, lv_color_hex(0x263340), 0);
    lv_obj_set_style_border_width(cover, 0, 0);
    lv_obj_set_style_clip_corner(cover, true, 0);
    remove_padding(cover);
    lv_obj_remove_flag(cover, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *disc = lv_obj_create(cover);
    lv_obj_set_pos(disc, 24, 18);
    lv_obj_set_size(disc, 82, 82);
    lv_obj_set_style_radius(disc, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(disc, lv_color_hex(0x18222D), 0);
    lv_obj_set_style_border_color(disc, lv_color_hex(0x6C8391), 0);
    lv_obj_set_style_border_width(disc, 4, 0);
    remove_padding(disc);
    lv_obj_remove_flag(disc, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *disc_center = lv_obj_create(disc);
    lv_obj_set_size(disc_center, 18, 18);
    lv_obj_center(disc_center);
    lv_obj_set_style_radius(disc_center, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(disc_center, lv_color_hex(0xA9BBC5), 0);
    lv_obj_set_style_border_width(disc_center, 0, 0);
    remove_padding(disc_center);
    lv_obj_remove_flag(disc_center, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *cover_caption = make_label(cover, "ALBUM ART", 18, 108, 94, 0xC5D0D6);
    lv_obj_set_style_text_align(cover_caption, LV_TEXT_ALIGN_CENTER, 0);

    ui->cover_image = lv_image_create(cover);
    lv_obj_set_pos(ui->cover_image, 0, 0);
    lv_obj_set_size(ui->cover_image, COVER_SIZE, COVER_SIZE);
    lv_obj_add_flag(ui->cover_image, LV_OBJ_FLAG_HIDDEN);

    ui->source = make_label(card, "YOUTUBE MUSIC", 158, 39, 116, 0xC1C9D0);
    ui->title = make_label(card, "Girls' Capitalism", 158, 62, 149, 0xFFFFFF);
    lv_obj_set_style_text_font(ui->title, &lv_font_montserrat_20, 0);
    // DOT keeps the title in its two-line box even for unusually long tracks.
    lv_label_set_long_mode(ui->title, LV_LABEL_LONG_DOT);
    lv_obj_set_height(ui->title, 45);
    ui->artist = make_label(card, "tripleS", 158, 109, 149, 0xD2D4DB);

    lv_obj_t *progress = lv_slider_create(card);
    ui->progress = progress;
    lv_obj_set_pos(progress, 157, 191);
    lv_obj_set_size(progress, 150, 4);
    lv_obj_set_ext_click_area(progress, 6);
    // Uniform enlargement while dragging stays inside the 320-pixel screen.
    lv_obj_set_style_transform_scale(progress, 290, LV_STATE_PRESSED);
    lv_obj_set_style_radius(progress, LV_RADIUS_CIRCLE, LV_PART_MAIN);
    lv_obj_set_style_radius(progress, LV_RADIUS_CIRCLE, LV_PART_INDICATOR);
    lv_obj_set_style_radius(progress, LV_RADIUS_CIRCLE, LV_PART_KNOB);
    lv_obj_set_style_bg_color(progress, lv_color_hex(0x494B55), LV_PART_MAIN);
    lv_obj_set_style_bg_color(progress, lv_color_hex(0xE5A4BA), LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(progress, lv_color_hex(0xB6C1C8),
                              LV_PART_MAIN | LV_STATE_PRESSED);
    lv_obj_set_style_bg_opa(progress, LV_OPA_TRANSP, LV_PART_KNOB);
    lv_obj_set_style_bg_opa(progress, LV_OPA_TRANSP,
                            LV_PART_KNOB | LV_STATE_PRESSED);
    lv_obj_set_style_pad_all(progress, 5, LV_PART_KNOB);
    lv_obj_set_style_border_width(progress, 0, LV_PART_KNOB);
    lv_obj_set_style_shadow_width(progress, 0, LV_PART_KNOB);
    lv_obj_set_style_outline_width(progress, 0, LV_PART_KNOB);
    lv_slider_set_range(progress, 0, 1000);
    lv_slider_set_value(progress, 0, LV_ANIM_OFF);

    ui->elapsed = make_label(card, "--:--", 157, 202, 54, 0xD0D6DB);
    ui->duration = make_label(card, "--:--", 242, 202, 65, 0xD0D6DB);
    lv_obj_set_style_text_align(ui->duration, LV_TEXT_ALIGN_RIGHT, 0);

    ui->previous_button = make_transport_button(card, 160, 136, 40,
                                                 &previous_icon_kind, NULL);
    ui->play_button = make_transport_button(card, 211, 132, 48,
                                             &play_pause_icon_kind, &ui->play_icon);
    lv_obj_add_state(ui->play_icon, LV_STATE_USER_1);
    ui->next_button = make_transport_button(card, 266, 136, 40,
                                             &next_icon_kind, NULL);

    ui->volume_button = make_round_button(card, LV_SYMBOL_VOLUME_MAX,
                                           235, 5, 28, 0x313440, 0xFFFFFF);
    ui->volume_icon = lv_obj_get_child(ui->volume_button, 0);
    lv_obj_set_style_text_font(ui->volume_icon, &lv_font_montserrat_14, 0);
    // Enlarge the speaker glyph uniformly while keeping the 28x28 touch target.
    lv_obj_set_style_transform_scale(ui->volume_icon, 320, 0);

    lv_obj_t *volume_panel = lv_obj_create(card);
    ui->volume_panel = volume_panel;
    // The popup sits entirely in the blank top band and never covers album
    // art, metadata, playback controls, or the speaker button to its right.
    // The slightly wider invisible container leaves room for the track's
    // pressed-state scale without clipping; its resting screen position stays
    // x104..215.
    lv_obj_set_pos(volume_panel, 90, 3);
    lv_obj_set_size(volume_panel, 140, 32);
    lv_obj_set_style_bg_opa(volume_panel, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(volume_panel, 0, 0);
    lv_obj_set_style_shadow_width(volume_panel, 0, 0);
    remove_padding(volume_panel);
    lv_obj_remove_flag(volume_panel, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *volume = lv_slider_create(volume_panel);
    ui->volume = volume;
    lv_obj_set_pos(volume, 14, 14);
    lv_obj_set_size(volume, 112, 4);
    lv_obj_set_ext_click_area(volume, 10);
    // Scale both axes equally while dragging so the track keeps its shape.
    lv_obj_set_style_transform_scale(volume, 310, LV_STATE_PRESSED);
    lv_obj_set_style_radius(volume, LV_RADIUS_CIRCLE, LV_PART_MAIN);
    lv_obj_set_style_radius(volume, LV_RADIUS_CIRCLE, LV_PART_INDICATOR);
    lv_obj_set_style_radius(volume, LV_RADIUS_CIRCLE, LV_PART_KNOB);
    lv_obj_set_style_bg_color(volume, lv_color_hex(0x494B55), LV_PART_MAIN);
    lv_obj_set_style_bg_color(volume, lv_color_hex(0xE5A4BA), LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(volume, lv_color_hex(0xB6C1C8),
                              LV_PART_MAIN | LV_STATE_PRESSED);
    lv_obj_set_style_bg_opa(volume, LV_OPA_TRANSP, LV_PART_KNOB);
    lv_obj_set_style_bg_opa(volume, LV_OPA_TRANSP,
                            LV_PART_KNOB | LV_STATE_PRESSED);
    lv_obj_set_style_pad_all(volume, 5, LV_PART_KNOB);
    lv_obj_set_style_border_width(volume, 0, LV_PART_KNOB);
    lv_obj_set_style_shadow_width(volume, 0, LV_PART_KNOB);
    lv_obj_set_style_outline_width(volume, 0, LV_PART_KNOB);
    lv_slider_set_range(volume, 0, 100);
    lv_slider_set_value(volume, 50, LV_ANIM_OFF);
    lv_obj_add_flag(volume_panel, LV_OBJ_FLAG_HIDDEN);

    apply_cover_theme(ui, NULL);
}

bool music_ui_set_cover(music_ui_t *ui, const uint8_t *pixels, size_t size)
{
    if (ui == NULL || ui->cover_image == NULL) return false;

    if (pixels == NULL && size == 0) {
        lv_obj_add_flag(ui->cover_image, LV_OBJ_FLAG_HIDDEN);
        lv_image_cache_drop(&ui->cover_descriptors[ui->cover_descriptor_index]);
        apply_cover_theme(ui, NULL);
        return true;
    }
    if (pixels == NULL || size != COVER_BYTES) return false;

    const uint8_t next = ui->cover_descriptor_index ^ 1u;
    lv_image_dsc_t *descriptor = &ui->cover_descriptors[next];
    lv_image_cache_drop(descriptor);
    memset(descriptor, 0, sizeof(*descriptor));
    descriptor->header.magic = LV_IMAGE_HEADER_MAGIC;
    descriptor->header.cf = LV_COLOR_FORMAT_RGB565;
    descriptor->header.w = COVER_SIZE;
    descriptor->header.h = COVER_SIZE;
    descriptor->header.stride = COVER_SIZE * 2;
    descriptor->data_size = COVER_BYTES;
    descriptor->data = pixels;

    lv_image_set_src(ui->cover_image, descriptor);
    lv_obj_remove_flag(ui->cover_image, LV_OBJ_FLAG_HIDDEN);
    lv_image_cache_drop(&ui->cover_descriptors[ui->cover_descriptor_index]);
    ui->cover_descriptor_index = next;
    apply_cover_theme(ui, pixels);
    return true;
}

static void format_time(char *out, size_t capacity, uint32_t milliseconds)
{
    const uint32_t seconds = milliseconds / 1000u;
    snprintf(out, capacity, "%u:%02u", (unsigned)(seconds / 60u),
             (unsigned)(seconds % 60u));
}

void music_ui_preview_seek(music_ui_t *ui, uint32_t elapsed_ms,
                           uint32_t duration_ms)
{
    if (ui == NULL) return;
    if (duration_ms == 0) {
        lv_label_set_text(ui->elapsed, "--:--");
        lv_label_set_text(ui->duration, "--:--");
        return;
    }

    if (elapsed_ms > duration_ms) elapsed_ms = duration_ms;
    char time_text[16];
    format_time(time_text, sizeof(time_text), elapsed_ms);
    lv_label_set_text(ui->elapsed, time_text);
    time_text[0] = '-';
    format_time(time_text + 1, sizeof(time_text) - 1,
                duration_ms - elapsed_ms + 500u);
    lv_label_set_text(ui->duration, time_text);
}

void music_ui_set_play_state(music_ui_t *ui, uint8_t state)
{
    if (ui == NULL || ui->play_icon == NULL) return;
    const bool playing = state == 1;
    if (lv_obj_has_state(ui->play_icon, LV_STATE_USER_1) == playing) return;
    if (playing) {
        lv_obj_add_state(ui->play_icon, LV_STATE_USER_1);
    } else {
        lv_obj_remove_state(ui->play_icon, LV_STATE_USER_1);
    }
    lv_obj_invalidate(ui->play_icon);
}

void music_ui_set_playback(music_ui_t *ui, uint8_t state, bool timeline_valid,
                           uint32_t elapsed_ms, uint32_t duration_ms)
{
    if (ui == NULL) return;
    music_ui_set_play_state(ui, state);

    if (!timeline_valid || duration_ms == 0) {
        lv_slider_set_value(ui->progress, 0, LV_ANIM_OFF);
        music_ui_preview_seek(ui, 0, 0);
        return;
    }

    if (elapsed_ms > duration_ms) elapsed_ms = duration_ms;
    const uint32_t permille =
        (uint32_t)((uint64_t)elapsed_ms * 1000u / duration_ms);
    lv_slider_set_value(ui->progress, (int32_t)permille, LV_ANIM_OFF);
    music_ui_preview_seek(ui, elapsed_ms, duration_ms);
}

void music_ui_set_volume(music_ui_t *ui, uint8_t percent)
{
    if (ui == NULL || ui->volume == NULL) return;
    if (percent > 100) percent = 100;
    // lv_slider_set_value delegates to lv_bar_set_value; LVGL only emits
    // VALUE_CHANGED for user slider interaction, not this programmatic update.
    lv_slider_set_value(ui->volume, percent, LV_ANIM_OFF);
    if (ui->volume_percent != NULL) {
        char text[8];
        snprintf(text, sizeof(text), "%u%%", (unsigned)percent);
        lv_label_set_text(ui->volume_percent, text);
    }
    if (ui->volume_icon != NULL) {
        const char *icon = percent == 0 ? LV_SYMBOL_MUTE :
                           percent < 50 ? LV_SYMBOL_VOLUME_MID :
                                          LV_SYMBOL_VOLUME_MAX;
        lv_label_set_text(ui->volume_icon, icon);
        lv_obj_center(ui->volume_icon);
    }
}

void music_ui_show_volume(music_ui_t *ui, bool show)
{
    if (ui == NULL || ui->volume_panel == NULL) return;
    if (show) {
        lv_obj_remove_flag(ui->volume_panel, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(ui->volume_panel, LV_OBJ_FLAG_HIDDEN);
    }
}
