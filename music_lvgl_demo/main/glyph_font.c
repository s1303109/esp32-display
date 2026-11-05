#include "glyph_font.h"

#include <string.h>

#include "esp_heap_caps.h"

#define STAGED_ASSET_TTL_MS 10000

static bool glyph_descriptor(const lv_font_t *font, lv_font_glyph_dsc_t *dsc,
                             uint32_t codepoint, uint32_t next_codepoint)
{
    (void)next_codepoint;
    const glyph_font_context_t *context = font->user_data;
    const glyph_font_set_t *set = context->set;
    if (!set->valid) return false;
    const glyph_asset_entry_t *entry =
        glyph_asset_find(set->entries, set->count, context->font_px, codepoint);
    if (entry == NULL) return false;

    dsc->adv_w = entry->adv_w;
    dsc->box_w = entry->box_w;
    dsc->box_h = entry->box_h;
    dsc->ofs_x = entry->ofs_x;
    dsc->ofs_y = entry->ofs_y;
    dsc->format = LV_FONT_GLYPH_FORMAT_A4;
    dsc->is_placeholder = false;
    dsc->gid.index = (uint32_t)(entry - set->entries) + 1u;
    dsc->entry = NULL;
    return true;
}

static const void *glyph_bitmap(lv_font_glyph_dsc_t *dsc,
                                 lv_draw_buf_t *draw_buf)
{
    const glyph_font_context_t *context = dsc->resolved_font->user_data;
    const glyph_font_set_t *set = context->set;
    if (!set->valid || dsc->gid.index == 0 ||
        dsc->gid.index > set->count || draw_buf == NULL) return NULL;
    const glyph_asset_entry_t *entry = &set->entries[dsc->gid.index - 1u];
    if (!glyph_asset_expand_a4(set->bytes, set->byte_size, entry,
                               draw_buf->data, draw_buf->data_size,
                               draw_buf->header.stride)) return NULL;
    return draw_buf;
}

static void init_font(glyph_font_set_t *set, lv_font_t *out,
                      glyph_font_context_t *context, uint8_t font_px)
{
    const lv_font_t *fallback = font_px == 20 ? &lv_font_montserrat_20 :
                                                &lv_font_montserrat_14;
    *out = *fallback;
    context->set = set;
    context->font_px = font_px;
    out->get_glyph_dsc = glyph_descriptor;
    out->get_glyph_bitmap = glyph_bitmap;
    out->release_glyph = NULL;
    out->dsc = NULL;
    out->fallback = fallback;
    out->user_data = context;
    out->kerning = LV_FONT_KERNING_NORMAL;
}

esp_err_t glyph_font_store_init(glyph_font_store_t *store)
{
    if (store == NULL) return ESP_ERR_INVALID_ARG;
    memset(store, 0, sizeof(*store));
    for (int i = 0; i < 2; i++) {
        glyph_font_set_t *set = &store->sets[i];
        set->bytes = heap_caps_malloc(GLYPH_ASSET_MAX_BYTES,
                                      MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        set->entries = heap_caps_malloc(
            GLYPH_ASSET_MAX_COUNT * sizeof(*set->entries),
            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (set->bytes == NULL || set->entries == NULL) {
            for (int j = 0; j <= i; j++) {
                heap_caps_free(store->sets[j].bytes);
                heap_caps_free(store->sets[j].entries);
                store->sets[j].bytes = NULL;
                store->sets[j].entries = NULL;
            }
            return ESP_ERR_NO_MEM;
        }
        init_font(set, &set->font14, &set->context14, 14);
        init_font(set, &set->font20, &set->context20, 20);
    }
    return ESP_OK;
}

bool glyph_font_stage(glyph_font_store_t *store, uint8_t track_id,
                      const uint8_t *data, size_t size)
{
    if (store == NULL || data == NULL || size > GLYPH_ASSET_MAX_BYTES) return false;
    const uint8_t index = store->active ? store->active_index ^ 1u :
                          store->staged ? store->staged_index : 0u;
    glyph_font_set_t *set = &store->sets[index];
    set->valid = false;
    uint16_t count = 0;
    if (!glyph_asset_parse(data, size, set->entries,
                           GLYPH_ASSET_MAX_COUNT, &count)) {
        store->staged = false;
        return false;
    }
    memcpy(set->bytes, data, size);
    set->byte_size = size;
    set->count = count;
    set->track_id = track_id;
    set->valid = true;
    store->staged_index = index;
    store->staged_tick = lv_tick_get();
    store->staged = true;
    return true;
}

bool glyph_font_commit(glyph_font_store_t *store, uint8_t track_id)
{
    if (store == NULL) return false;
    if (store->staged &&
        lv_tick_get() - store->staged_tick > STAGED_ASSET_TTL_MS) {
        store->staged = false;
    }
    if (!store->staged || !store->sets[store->staged_index].valid ||
        store->sets[store->staged_index].track_id != track_id) {
        store->active = false;
        return false;
    }
    store->active_index = store->staged_index;
    store->active = true;
    store->staged = false;
    return true;
}

const lv_font_t *glyph_font_active_14(const glyph_font_store_t *store)
{
    return store != NULL && store->active ?
           &store->sets[store->active_index].font14 : &lv_font_montserrat_14;
}

const lv_font_t *glyph_font_active_20(const glyph_font_store_t *store)
{
    return store != NULL && store->active ?
           &store->sets[store->active_index].font20 : &lv_font_montserrat_20;
}

uint16_t glyph_font_active_count(const glyph_font_store_t *store)
{
    return store != NULL && store->active ?
           store->sets[store->active_index].count : 0;
}
