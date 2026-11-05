#include "glyph_asset.h"

#include <string.h>

#define ASSET_HEADER_BYTES 6
#define GLYPH_HEADER_BYTES 12

static uint16_t read_le16(const uint8_t *data)
{
    return (uint16_t)data[0] | ((uint16_t)data[1] << 8);
}

static uint32_t read_le32(const uint8_t *data)
{
    return (uint32_t)data[0] | ((uint32_t)data[1] << 8) |
           ((uint32_t)data[2] << 16) | ((uint32_t)data[3] << 24);
}

bool glyph_asset_parse(const uint8_t *data, size_t size,
                       glyph_asset_entry_t *entries, size_t capacity,
                       uint16_t *count_out)
{
    if (data == NULL || entries == NULL || count_out == NULL ||
        size < ASSET_HEADER_BYTES || size > GLYPH_ASSET_MAX_BYTES ||
        memcmp(data, "GLY1", 4) != 0) return false;

    const uint16_t count = read_le16(data + 4);
    if (count > GLYPH_ASSET_MAX_COUNT || count > capacity) return false;

    size_t cursor = ASSET_HEADER_BYTES;
    for (uint16_t i = 0; i < count; i++) {
        if (size - cursor < GLYPH_HEADER_BYTES) return false;
        const uint8_t *header = data + cursor;
        const uint8_t font_px = header[0];
        const uint32_t codepoint = read_le32(header + 1);
        const uint8_t adv_w = header[5];
        const uint8_t box_w = header[6];
        const uint8_t box_h = header[7];
        const int8_t ofs_x = (int8_t)header[8];
        const int8_t ofs_y = (int8_t)header[9];
        const uint16_t data_len = read_le16(header + 10);
        cursor += GLYPH_HEADER_BYTES;

        const uint32_t expected_len = ((uint32_t)box_w + 1u) / 2u * box_h;
        if ((font_px != 14 && font_px != 20) ||
            codepoint == 0 || codepoint > 0x10ffffu ||
            (codepoint >= 0xd800u && codepoint <= 0xdfffu) ||
            box_w > 64 || box_h > 64 ||
            expected_len != data_len || data_len > size - cursor) {
            return false;
        }
        for (uint16_t prior = 0; prior < i; prior++) {
            if (entries[prior].font_px == font_px &&
                entries[prior].codepoint == codepoint) return false;
        }
        entries[i] = (glyph_asset_entry_t){
            .codepoint = codepoint,
            .bitmap_offset = (uint16_t)cursor,
            .bitmap_size = data_len,
            .font_px = font_px,
            .adv_w = adv_w,
            .box_w = box_w,
            .box_h = box_h,
            .ofs_x = ofs_x,
            .ofs_y = ofs_y,
        };
        cursor += data_len;
    }
    if (cursor != size) return false;
    *count_out = count;
    return true;
}

const glyph_asset_entry_t *glyph_asset_find(const glyph_asset_entry_t *entries,
                                            uint16_t count, uint8_t font_px,
                                            uint32_t codepoint)
{
    if (entries == NULL) return NULL;
    for (uint16_t i = 0; i < count; i++) {
        if (entries[i].font_px == font_px &&
            entries[i].codepoint == codepoint) return &entries[i];
    }
    return NULL;
}

bool glyph_asset_expand_a4(const uint8_t *asset, size_t asset_size,
                           const glyph_asset_entry_t *entry,
                           uint8_t *dest, size_t dest_size, size_t stride)
{
    if (asset == NULL || entry == NULL || dest == NULL ||
        entry->bitmap_offset > asset_size ||
        entry->bitmap_size > asset_size - entry->bitmap_offset ||
        stride < entry->box_w ||
        (size_t)entry->box_h * stride > dest_size) return false;

    const size_t source_stride = ((size_t)entry->box_w + 1u) / 2u;
    if (source_stride * entry->box_h != entry->bitmap_size) return false;
    const uint8_t *source = asset + entry->bitmap_offset;
    for (uint8_t y = 0; y < entry->box_h; y++) {
        for (uint8_t x = 0; x < entry->box_w; x++) {
            const uint8_t packed = source[(size_t)y * source_stride + x / 2u];
            const uint8_t alpha4 = (x & 1u) ? packed & 0x0fu : packed >> 4;
            dest[(size_t)y * stride + x] = alpha4 * 17u;
        }
    }
    return true;
}
