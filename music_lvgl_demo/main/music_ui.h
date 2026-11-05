#pragma once

#include "lvgl.h"

typedef struct {
    lv_obj_t *screen;
    lv_obj_t *card;
    lv_obj_t *cover;
    lv_obj_t *cover_image;
    lv_image_dsc_t cover_descriptors[2];
    uint8_t cover_descriptor_index;
    lv_obj_t *source;
    lv_obj_t *title;
    lv_obj_t *artist;
    lv_obj_t *progress; // LVGL slider, range 0..1000, no visible knob.
    lv_obj_t *elapsed;
    lv_obj_t *duration;
    lv_obj_t *previous_button;
    lv_obj_t *play_button;
    lv_obj_t *play_icon; // Custom-drawn play/pause geometry inside play_button.
    lv_obj_t *next_button;
    lv_obj_t *volume_button;
    lv_obj_t *volume_icon;
    lv_obj_t *volume_panel;
    lv_obj_t *volume_percent; // NULL in the compact, number-free layout.
    // LVGL slider inside the hidden, borderless top band, range 0..100.
    lv_obj_t *volume;
} music_ui_t;

// Build the static 320 x 240 music page on an existing LVGL screen.
// All returned object handles belong to the screen and remain valid until
// the screen or its children are deleted. Call from the LVGL task/lock.
void music_ui_create(lv_obj_t *screen, music_ui_t *ui);

// The RGB565 pixel buffer is owned by the BLE component and must remain
// unchanged while it is displayed. Pass NULL/0 to show the placeholder art.
bool music_ui_set_cover(music_ui_t *ui, const uint8_t *pixels, size_t size);

// state: 0 unknown, 1 playing, 2 paused, 3 stopped. Invalid timelines show
// --:-- and an empty progress slider while still updating the play icon.
void music_ui_set_playback(music_ui_t *ui, uint8_t state, bool timeline_valid,
                           uint32_t elapsed_ms, uint32_t duration_ms);

// Update only the elapsed and negative remaining labels while dragging the
// progress slider. The caller owns the slider's current value during a drag.
void music_ui_preview_seek(music_ui_t *ui, uint32_t elapsed_ms,
                           uint32_t duration_ms);

// Update only the play/pause icon from the current PC state; leave the
// progress slider and timeline labels untouched while a seek is pending.
void music_ui_set_play_state(music_ui_t *ui, uint8_t state);

// Swap text fonts before publishing a track's labels. The title uses title_font;
// artist and album/source use secondary_font. Existing layout is preserved.
void music_ui_set_text_fonts(music_ui_t *ui, const lv_font_t *title_font,
                             const lv_font_t *secondary_font);

// Update the volume slider from a Windows volume report. This programmatic
// value update does not dispatch the slider's user VALUE_CHANGED event.
void music_ui_set_volume(music_ui_t *ui, uint8_t percent);

// Show or hide the volume control opened from the speaker button.
void music_ui_show_volume(music_ui_t *ui, bool show);
