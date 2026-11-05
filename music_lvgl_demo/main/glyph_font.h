#pragma once

#include "glyph_asset.h"
#include "lvgl.h"
#include "esp_err.h"

typedef struct glyph_font_set glyph_font_set_t;

typedef struct {
    glyph_font_set_t *set;
    uint8_t font_px;
} glyph_font_context_t;

struct glyph_font_set {
    uint8_t *bytes;
    glyph_asset_entry_t *entries;
    size_t byte_size;
    uint16_t count;
    uint8_t track_id;
    bool valid;
    glyph_font_context_t context14;
    glyph_font_context_t context20;
    lv_font_t font14;
    lv_font_t font20;
};

typedef struct {
    glyph_font_set_t sets[2];
    uint8_t active_index;
    uint8_t staged_index;
    uint32_t staged_tick;
    bool active;
    bool staged;
} glyph_font_store_t;

/* Call once on the LVGL owner task after lv_init(). Two 32-KiB glyph pools
 * and two 192-entry tables are placed in PSRAM. */
esp_err_t glyph_font_store_init(glyph_font_store_t *store);

/* Parse and copy a complete CRC-checked GLY1 payload into the inactive pool. */
bool glyph_font_stage(glyph_font_store_t *store, uint8_t track_id,
                      const uint8_t *data, size_t size);

/* Activate the matching staged asset, or use the built-in fonts if none was
 * supplied for this track. A different track's staged asset is retained. */
bool glyph_font_commit(glyph_font_store_t *store, uint8_t track_id);

const lv_font_t *glyph_font_active_14(const glyph_font_store_t *store);
const lv_font_t *glyph_font_active_20(const glyph_font_store_t *store);
uint16_t glyph_font_active_count(const glyph_font_store_t *store);
