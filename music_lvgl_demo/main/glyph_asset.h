#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define GLYPH_ASSET_MAX_COUNT 192
#define GLYPH_ASSET_MAX_BYTES 32768

typedef struct {
    uint32_t codepoint;
    uint16_t bitmap_offset;
    uint16_t bitmap_size;
    uint8_t font_px;
    uint8_t adv_w;
    uint8_t box_w;
    uint8_t box_h;
    int8_t ofs_x;
    int8_t ofs_y;
} glyph_asset_entry_t;

/* Validate a complete GLY1 blob and locate each row-padded A4 bitmap.
 * Entries are only usable if the function returns true. */
bool glyph_asset_parse(const uint8_t *data, size_t size,
                       glyph_asset_entry_t *entries, size_t capacity,
                       uint16_t *count_out);

const glyph_asset_entry_t *glyph_asset_find(const glyph_asset_entry_t *entries,
                                            uint16_t count, uint8_t font_px,
                                            uint32_t codepoint);

/* Expand row-padded high-nibble-first A4 to LVGL's A8 draw buffer. */
bool glyph_asset_expand_a4(const uint8_t *asset, size_t asset_size,
                           const glyph_asset_entry_t *entry,
                           uint8_t *dest, size_t dest_size, size_t stride);
