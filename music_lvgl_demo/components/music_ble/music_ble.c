#include "music_ble.h"

#include <stdint.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "host/ble_att.h"
#include "host/ble_hs.h"
#include "host/ble_hs_id.h"
#include "host/ble_uuid.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "nvs_flash.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

#define DEVICE_NAME "ESP32-Music-Display"
#define FRAME_VERSION 1
#define COVER_FAST_VERSION 2
#define FRAME_HEADER_LEN 6
#define COVER_FAST_COMMIT_LEN 10
#define MAX_METADATA_LEN 512
#define MAX_GATT_FRAME_LEN 244
#define COVER_PREFERRED_MTU 247
#define PLAYBACK_FRAME_LEN 12
#define PLAYBACK_VOLUME_FRAME_LEN 13
#define PLAYBACK_TIMELINE_VALID 0x01
#define MAX_TIMELINE_MS (24UL * 60UL * 60UL * 1000UL)

/* Connection interval units are 1.25 ms; supervision timeout units are 10 ms.
 * Windows is the central and may accept, adjust, or reject these requests. */
static const struct ble_gap_upd_params fast_conn_params = {
    .itvl_min = 6,   /* 7.5 ms */
    .itvl_max = 12,  /* 15 ms */
    .latency = 0,
    .supervision_timeout = 400, /* 4 s */
};
static const struct ble_gap_upd_params fallback_conn_params = {
    .itvl_min = 12,  /* 15 ms */
    .itvl_max = 24,  /* 30 ms */
    .latency = 0,
    .supervision_timeout = 400, /* 4 s */
};

static const char *TAG = "music_ble";

/* BLE_UUID128_INIT takes bytes in little-endian order. */
static const ble_uuid128_t service_uuid = BLE_UUID128_INIT(
    0x9c, 0x9c, 0xde, 0x2c, 0xda, 0xb6, 0x7a, 0x8a,
    0x94, 0x45, 0xea, 0x4e, 0x0f, 0x46, 0xcb, 0xd1);
static const ble_uuid128_t metadata_uuid = BLE_UUID128_INIT(
    0xbe, 0xc7, 0xed, 0xb2, 0x9b, 0x04, 0x5d, 0xa7,
    0xca, 0x44, 0xe0, 0x4f, 0xcd, 0xdd, 0x47, 0xe9);
/* 0e60fb2f-ea19-4df2-a32a-19f81145ac17 */
static const ble_uuid128_t cover_uuid = BLE_UUID128_INIT(
    0x17, 0xac, 0x45, 0x11, 0xf8, 0x19, 0x2a, 0xa3,
    0xf2, 0x4d, 0x19, 0xea, 0x2f, 0xfb, 0x60, 0x0e);
/* 9c950662-05e7-4e55-a39e-0658ccb0c8fb */
static const ble_uuid128_t playback_uuid = BLE_UUID128_INIT(
    0xfb, 0xc8, 0xb0, 0xcc, 0x58, 0x06, 0x9e, 0xa3,
    0x55, 0x4e, 0xe7, 0x05, 0x62, 0x06, 0x95, 0x9c);
/* 4b717b37-4a03-4aa2-9dfc-a4bb88dfc905 */
static const ble_uuid128_t control_uuid = BLE_UUID128_INIT(
    0x05, 0xc9, 0xdf, 0x88, 0xbb, 0xa4, 0xfc, 0x9d,
    0xa2, 0x4a, 0x03, 0x4a, 0x37, 0x7b, 0x71, 0x4b);

static QueueHandle_t s_updates;
static QueueHandle_t s_cover_updates;
static QueueHandle_t s_playback_updates;
static uint8_t s_own_addr_type;
static uint16_t s_connected_handle = BLE_HS_CONN_HANDLE_NONE;
static uint16_t s_control_value_handle;
/* GAP callbacks run on the NimBLE task, while button clicks run on the LVGL
 * task. Protect this small cross-task snapshot independently of NimBLE's
 * internal host lock (which ble_gatts_notify_custom obtains itself). */
static portMUX_TYPE s_control_state_lock = portMUX_INITIALIZER_UNLOCKED;
static uint16_t s_control_conn_handle = BLE_HS_CONN_HANDLE_NONE;
static bool s_control_subscribed;
static enum {
    CONN_PARAM_IDLE,
    CONN_PARAM_FAST_PENDING,
    CONN_PARAM_FALLBACK_PENDING,
    CONN_PARAM_DONE,
} s_conn_param_state;
static uint8_t *s_cover_buffers[2];
static portMUX_TYPE s_cover_lock = portMUX_INITIALIZER_UNLOCKED;
/* -1 means none. -2 means a clear-cover update is pending. */
static int8_t s_cover_active_index = -1;
static int8_t s_cover_pending_index = -1;

/* Only the NimBLE host task writes this reassembly state. */
static struct {
    uint16_t conn_handle;
    uint16_t received;
    uint8_t image_id;
    uint8_t version;
    int8_t index;
    bool active;
} s_cover_rx;

/* GAP and GATT callbacks run on the NimBLE host task; no LVGL calls here. */
static struct {
    uint16_t conn_handle;
    uint16_t total_len;
    uint16_t received;
    uint8_t message_id;
    uint8_t bytes[MAX_METADATA_LEN];
    bool active;
} s_assembly;

static bool take_field(char *dst, size_t dst_cap,
                       const uint8_t **cursor, const uint8_t *end)
{
    const uint8_t *nul = memchr(*cursor, 0, (size_t)(end - *cursor));
    if (nul == NULL || (size_t)(nul - *cursor) >= dst_cap) {
        return false;
    }
    size_t length = (size_t)(nul - *cursor);
    memcpy(dst, *cursor, length);
    dst[length] = '\0';
    *cursor = nul + 1;
    return true;
}

static int queue_complete_metadata(void)
{
    const uint8_t *cursor = s_assembly.bytes;
    const uint8_t *end = cursor + s_assembly.total_len;
    music_ble_metadata_t update = {0};
    update.message_id = s_assembly.message_id;

    if (!take_field(update.title, sizeof(update.title), &cursor, end) ||
        !take_field(update.artist, sizeof(update.artist), &cursor, end) ||
        !take_field(update.album, sizeof(update.album), &cursor, end) ||
        cursor != end) {
        return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
    }
    /* The queue has one slot because only the newest track matters. */
    if (xQueueOverwrite(s_updates, &update) != pdPASS) {
        return BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    return 0;
}

static int metadata_access(uint16_t conn_handle, uint16_t attr_handle,
                           struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    (void)attr_handle;
    (void)arg;
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) {
        return BLE_ATT_ERR_REQ_NOT_SUPPORTED;
    }

    uint16_t frame_len = OS_MBUF_PKTLEN(ctxt->om);
    if (frame_len <= FRAME_HEADER_LEN || frame_len > MAX_GATT_FRAME_LEN) {
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }
    uint8_t frame[MAX_GATT_FRAME_LEN];
    if (ble_hs_mbuf_to_flat(ctxt->om, frame, sizeof(frame), NULL) != 0) {
        return BLE_ATT_ERR_UNLIKELY;
    }
    if (frame[0] != FRAME_VERSION) {
        return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
    }

    uint8_t message_id = frame[1];
    uint16_t offset = (uint16_t)frame[2] | ((uint16_t)frame[3] << 8);
    uint16_t total_len = (uint16_t)frame[4] | ((uint16_t)frame[5] << 8);
    uint16_t chunk_len = frame_len - FRAME_HEADER_LEN;
    if (total_len < 3 || total_len > MAX_METADATA_LEN ||
        offset > total_len || chunk_len > total_len - offset) {
        s_assembly.active = false;
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }

    if (offset == 0) {
        s_assembly.conn_handle = conn_handle;
        s_assembly.message_id = message_id;
        s_assembly.total_len = total_len;
        s_assembly.received = 0;
        s_assembly.active = true;
    }
    if (!s_assembly.active || s_assembly.conn_handle != conn_handle ||
        s_assembly.message_id != message_id || s_assembly.total_len != total_len ||
        offset != s_assembly.received) {
        s_assembly.active = false;
        return BLE_ATT_ERR_INVALID_OFFSET;
    }

    memcpy(s_assembly.bytes + offset, frame + FRAME_HEADER_LEN, chunk_len);
    s_assembly.received += chunk_len;
    if (s_assembly.received == s_assembly.total_len) {
        s_assembly.active = false;
        return queue_complete_metadata();
    }
    return 0;
}

static int queue_cover_update(int8_t index, uint8_t image_id)
{
    music_ble_cover_t update = {
        .pixels = index >= 0 ? s_cover_buffers[index] : NULL,
        .size = index >= 0 ? MUSIC_BLE_COVER_BYTES : 0,
        .image_id = image_id,
    };

    portENTER_CRITICAL(&s_cover_lock);
    if (s_cover_pending_index != -1) {
        portEXIT_CRITICAL(&s_cover_lock);
        return BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    s_cover_pending_index = index;
    portEXIT_CRITICAL(&s_cover_lock);

    if (xQueueSend(s_cover_updates, &update, 0) != pdPASS) {
        portENTER_CRITICAL(&s_cover_lock);
        s_cover_pending_index = -1;
        portEXIT_CRITICAL(&s_cover_lock);
        return BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    return 0;
}

static uint32_t cover_crc32(const uint8_t *bytes, size_t size)
{
    uint32_t crc = UINT32_MAX;
    for (size_t i = 0; i < size; ++i) {
        crc ^= bytes[i];
        for (unsigned bit = 0; bit < 8; ++bit) {
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
        }
    }
    return ~crc;
}

static int cover_access(uint16_t conn_handle, uint16_t attr_handle,
                        struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    (void)attr_handle;
    (void)arg;
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) {
        return BLE_ATT_ERR_REQ_NOT_SUPPORTED;
    }

    uint16_t frame_len = OS_MBUF_PKTLEN(ctxt->om);
    if (frame_len < FRAME_HEADER_LEN || frame_len > MAX_GATT_FRAME_LEN) {
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }
    uint8_t frame[MAX_GATT_FRAME_LEN];
    if (ble_hs_mbuf_to_flat(ctxt->om, frame, sizeof(frame), NULL) != 0) {
        return BLE_ATT_ERR_UNLIKELY;
    }
    const uint8_t version = frame[0];
    if (version != FRAME_VERSION && version != COVER_FAST_VERSION) {
        return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
    }

    uint8_t image_id = frame[1];
    uint16_t offset = (uint16_t)frame[2] | ((uint16_t)frame[3] << 8);
    uint16_t total_len = (uint16_t)frame[4] | ((uint16_t)frame[5] << 8);
    if (total_len == 0) {
        if (version != FRAME_VERSION || frame_len != FRAME_HEADER_LEN || offset != 0) {
            return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
        }
        s_cover_rx.active = false;
        return queue_cover_update(-2, image_id);
    }
    /* A failed fast transfer may leave write commands queued in Windows.
     * Once reliable v1 recovery has begun, ignore any late v2 frames rather
     * than letting them reset or corrupt the recovery assembly. */
    if (s_cover_rx.active && s_cover_rx.version == FRAME_VERSION &&
        s_cover_rx.conn_handle == conn_handle && version == COVER_FAST_VERSION) {
        return BLE_ATT_ERR_INVALID_OFFSET;
    }
    /* Fast write commands are committed by one write request carrying the
     * CRC32. The image remains hidden until every byte is verified. */
    if (version == COVER_FAST_VERSION && offset == total_len) {
        if (frame_len != COVER_FAST_COMMIT_LEN ||
            total_len != MUSIC_BLE_COVER_BYTES ||
            !s_cover_rx.active || s_cover_rx.version != version ||
            s_cover_rx.conn_handle != conn_handle ||
            s_cover_rx.image_id != image_id ||
            s_cover_rx.received != total_len) {
            s_cover_rx.active = false;
            return BLE_ATT_ERR_INVALID_OFFSET;
        }
        const uint32_t expected_crc = (uint32_t)frame[6] |
                                      ((uint32_t)frame[7] << 8) |
                                      ((uint32_t)frame[8] << 16) |
                                      ((uint32_t)frame[9] << 24);
        const uint32_t actual_crc = cover_crc32(s_cover_buffers[s_cover_rx.index],
                                                 MUSIC_BLE_COVER_BYTES);
        if (actual_crc != expected_crc) {
            ESP_LOGW(TAG, "fast cover CRC mismatch: got=%08lx expected=%08lx",
                     (unsigned long)actual_crc, (unsigned long)expected_crc);
            s_cover_rx.active = false;
            return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
        }
        const int result = queue_cover_update(s_cover_rx.index, image_id);
        if (result == 0) s_cover_rx.active = false;
        return result;
    }
    if (total_len != MUSIC_BLE_COVER_BYTES || frame_len == FRAME_HEADER_LEN ||
        offset >= total_len || frame_len - FRAME_HEADER_LEN > total_len - offset) {
        s_cover_rx.active = false;
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }

    if (offset == 0) {
        portENTER_CRITICAL(&s_cover_lock);
        int8_t pending_index = s_cover_pending_index;
        int8_t active_index = s_cover_active_index;
        portEXIT_CRITICAL(&s_cover_lock);
        if (pending_index != -1) {
            return BLE_ATT_ERR_INSUFFICIENT_RES;
        }
        s_cover_rx.conn_handle = conn_handle;
        s_cover_rx.received = 0;
        s_cover_rx.image_id = image_id;
        s_cover_rx.version = version;
        s_cover_rx.index = active_index == 0 ? 1 : 0;
        s_cover_rx.active = true;
    }
    if (!s_cover_rx.active || s_cover_rx.conn_handle != conn_handle ||
        s_cover_rx.image_id != image_id || s_cover_rx.version != version ||
        offset != s_cover_rx.received) {
        s_cover_rx.active = false;
        return BLE_ATT_ERR_INVALID_OFFSET;
    }

    uint16_t chunk_len = frame_len - FRAME_HEADER_LEN;
    memcpy(s_cover_buffers[s_cover_rx.index] + offset,
           frame + FRAME_HEADER_LEN, chunk_len);
    s_cover_rx.received += chunk_len;
    if (version == FRAME_VERSION && s_cover_rx.received == MUSIC_BLE_COVER_BYTES) {
        s_cover_rx.active = false;
        return queue_cover_update(s_cover_rx.index, image_id);
    }
    return 0;
}

static uint32_t read_le32(const uint8_t *bytes)
{
    return (uint32_t)bytes[0] | ((uint32_t)bytes[1] << 8) |
           ((uint32_t)bytes[2] << 16) | ((uint32_t)bytes[3] << 24);
}

static int playback_access(uint16_t conn_handle, uint16_t attr_handle,
                           struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    (void)conn_handle;
    (void)attr_handle;
    (void)arg;
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) {
        return BLE_ATT_ERR_REQ_NOT_SUPPORTED;
    }
    const uint16_t frame_len = OS_MBUF_PKTLEN(ctxt->om);
    if (frame_len != PLAYBACK_FRAME_LEN &&
        frame_len != PLAYBACK_VOLUME_FRAME_LEN) {
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }

    uint8_t frame[PLAYBACK_VOLUME_FRAME_LEN] = {0};
    if (ble_hs_mbuf_to_flat(ctxt->om, frame, sizeof(frame), NULL) != 0) {
        return BLE_ATT_ERR_UNLIKELY;
    }
    if (frame[0] != FRAME_VERSION || frame[2] > 3 ||
        (frame[3] & ~PLAYBACK_TIMELINE_VALID) != 0 ||
        (frame_len == PLAYBACK_VOLUME_FRAME_LEN && frame[12] > 100)) {
        return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
    }

    music_ble_playback_t update = {
        .track_id = frame[1],
        .state = frame[2],
        .timeline_valid = (frame[3] & PLAYBACK_TIMELINE_VALID) != 0,
        .elapsed_ms = read_le32(frame + 4),
        .duration_ms = read_le32(frame + 8),
        .volume_valid = frame_len == PLAYBACK_VOLUME_FRAME_LEN,
        .volume_percent = frame_len == PLAYBACK_VOLUME_FRAME_LEN ? frame[12] : 0,
    };
    if (update.timeline_valid) {
        if (update.duration_ms > MAX_TIMELINE_MS) {
            update.duration_ms = MAX_TIMELINE_MS;
        }
        if (update.elapsed_ms > update.duration_ms) {
            update.elapsed_ms = update.duration_ms;
        }
    } else {
        update.elapsed_ms = 0;
        update.duration_ms = 0;
    }
    return xQueueOverwrite(s_playback_updates, &update) == pdPASS
               ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
}

static int control_access(uint16_t conn_handle, uint16_t attr_handle,
                          struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    (void)conn_handle;
    (void)attr_handle;
    (void)ctxt;
    (void)arg;
    return BLE_ATT_ERR_REQ_NOT_SUPPORTED;
}

static const struct ble_gatt_svc_def services[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &service_uuid.u,
        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                .uuid = &metadata_uuid.u,
                .access_cb = metadata_access,
                .flags = BLE_GATT_CHR_F_WRITE,
            },
            {
                .uuid = &cover_uuid.u,
                .access_cb = cover_access,
                .flags = BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP,
            },
            {
                .uuid = &playback_uuid.u,
                .access_cb = playback_access,
                .flags = BLE_GATT_CHR_F_WRITE,
            },
            {
                .uuid = &control_uuid.u,
                .access_cb = control_access,
                .val_handle = &s_control_value_handle,
                .flags = BLE_GATT_CHR_F_NOTIFY,
            },
            {0},
        },
    },
    {0},
};

static void start_advertising(void);

static void log_connection_params(uint16_t handle, const char *stage)
{
    struct ble_gap_conn_desc desc;
    int rc = ble_gap_conn_find(handle, &desc);
    if (rc != 0) {
        ESP_LOGW(TAG, "%s: cannot read connection parameters: %d", stage, rc);
        return;
    }
    uint32_t interval_us = (uint32_t)desc.conn_itvl * 1250;
    ESP_LOGI(TAG, "%s: interval=%u.%03u ms, latency=%u, timeout=%u ms",
             stage, (unsigned)(interval_us / 1000),
             (unsigned)(interval_us % 1000), (unsigned)desc.conn_latency,
             (unsigned)desc.supervision_timeout * 10);
}

static void request_fallback_params(uint16_t handle)
{
    s_conn_param_state = CONN_PARAM_FALLBACK_PENDING;
    int rc = ble_gap_update_params(handle, &fallback_conn_params);
    if (rc != 0) {
        ESP_LOGW(TAG, "15-30 ms connection interval request failed: %d", rc);
        s_conn_param_state = CONN_PARAM_DONE;
    } else {
        ESP_LOGI(TAG, "requested 15-30 ms connection interval, latency 0");
    }
}

static int gap_event(struct ble_gap_event *event, void *arg)
{
    (void)arg;
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status == 0) {
            ESP_LOGI(TAG, "connected, handle=%u", event->connect.conn_handle);
            s_connected_handle = event->connect.conn_handle;
            portENTER_CRITICAL(&s_control_state_lock);
            s_control_conn_handle = event->connect.conn_handle;
            s_control_subscribed = false;
            portEXIT_CRITICAL(&s_control_state_lock);
            log_connection_params(s_connected_handle, "connected");

            s_conn_param_state = CONN_PARAM_FAST_PENDING;
            int rc = ble_gap_update_params(s_connected_handle, &fast_conn_params);
            if (rc != 0) {
                ESP_LOGW(TAG, "7.5-15 ms connection interval request failed: %d", rc);
                request_fallback_params(s_connected_handle);
            } else {
                ESP_LOGI(TAG, "requested 7.5-15 ms connection interval, latency 0");
            }

            uint8_t tx_phy = 0;
            uint8_t rx_phy = 0;
            rc = ble_gap_read_le_phy(s_connected_handle, &tx_phy, &rx_phy);
            if (rc == 0) {
                ESP_LOGI(TAG, "PHY: TX=%u RX=%u (1=1M, 2=2M, 3=Coded)",
                         (unsigned)tx_phy, (unsigned)rx_phy);
            } else {
                ESP_LOGW(TAG, "PHY read failed: %d", rc);
            }
        } else {
            start_advertising();
        }
        break;
    case BLE_GAP_EVENT_DISCONNECT:
        s_assembly.active = false;
        s_cover_rx.active = false;
        s_connected_handle = BLE_HS_CONN_HANDLE_NONE;
        portENTER_CRITICAL(&s_control_state_lock);
        s_control_conn_handle = BLE_HS_CONN_HANDLE_NONE;
        s_control_subscribed = false;
        portEXIT_CRITICAL(&s_control_state_lock);
        s_conn_param_state = CONN_PARAM_IDLE;
        ESP_LOGI(TAG, "disconnected, reason=%d", event->disconnect.reason);
        start_advertising();
        break;
    case BLE_GAP_EVENT_SUBSCRIBE:
        if (event->subscribe.attr_handle == s_control_value_handle) {
            portENTER_CRITICAL(&s_control_state_lock);
            if (event->subscribe.conn_handle == s_control_conn_handle) {
                s_control_subscribed = event->subscribe.cur_notify != 0;
            }
            portEXIT_CRITICAL(&s_control_state_lock);
            ESP_LOGI(TAG, "control notifications %s",
                     event->subscribe.cur_notify ? "enabled" : "disabled");
        }
        break;
    case BLE_GAP_EVENT_CONN_UPDATE:
        ESP_LOGI(TAG, "connection update result=%d", event->conn_update.status);
        log_connection_params(event->conn_update.conn_handle, "updated");
        if (event->conn_update.conn_handle == s_connected_handle) {
            if (s_conn_param_state == CONN_PARAM_FAST_PENDING &&
                event->conn_update.status != 0) {
                request_fallback_params(s_connected_handle);
            } else if (s_conn_param_state == CONN_PARAM_FAST_PENDING ||
                       s_conn_param_state == CONN_PARAM_FALLBACK_PENDING) {
                s_conn_param_state = CONN_PARAM_DONE;
            }
        }
        break;
    case BLE_GAP_EVENT_MTU:
        ESP_LOGI(TAG, "ATT MTU=%u, handle=%u",
                 (unsigned)event->mtu.value, (unsigned)event->mtu.conn_handle);
        break;
    case BLE_GAP_EVENT_PHY_UPDATE_COMPLETE:
        ESP_LOGI(TAG, "PHY update result=%d, TX=%u RX=%u (1=1M, 2=2M, 3=Coded)",
                 event->phy_updated.status, (unsigned)event->phy_updated.tx_phy,
                 (unsigned)event->phy_updated.rx_phy);
        break;
    case BLE_GAP_EVENT_ADV_COMPLETE:
        start_advertising();
        break;
    default:
        break;
    }
    return 0;
}

static void start_advertising(void)
{
    struct ble_hs_adv_fields fields = {0};
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.name = (uint8_t *)DEVICE_NAME;
    fields.name_len = strlen(DEVICE_NAME);
    fields.name_is_complete = 1;
    int rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) {
        ESP_LOGE(TAG, "advertising fields failed: %d", rc);
        return;
    }

    struct ble_gap_adv_params params = {0};
    params.conn_mode = BLE_GAP_CONN_MODE_UND;
    params.disc_mode = BLE_GAP_DISC_MODE_GEN;
    rc = ble_gap_adv_start(s_own_addr_type, NULL, BLE_HS_FOREVER,
                           &params, gap_event, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "advertising start failed: %d", rc);
    }
}

static void on_sync(void)
{
    int rc = ble_hs_util_ensure_addr(0);
    if (rc == 0) {
        rc = ble_hs_id_infer_auto(0, &s_own_addr_type);
    }
    if (rc != 0) {
        ESP_LOGE(TAG, "BLE address setup failed: %d", rc);
        return;
    }
    start_advertising();
}

static void host_task(void *param)
{
    (void)param;
    nimble_port_run();
    nimble_port_freertos_deinit();
}

esp_err_t music_ble_start(void)
{
    esp_err_t err = ESP_OK;
    if (s_updates != NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    s_updates = xQueueCreate(1, sizeof(music_ble_metadata_t));
    if (s_updates == NULL) {
        return ESP_ERR_NO_MEM;
    }

    s_cover_updates = xQueueCreate(1, sizeof(music_ble_cover_t));
    s_playback_updates = xQueueCreate(1, sizeof(music_ble_playback_t));
    s_cover_buffers[0] = heap_caps_malloc(MUSIC_BLE_COVER_BYTES,
                                           MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    s_cover_buffers[1] = heap_caps_malloc(MUSIC_BLE_COVER_BYTES,
                                           MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (s_cover_updates == NULL || s_playback_updates == NULL ||
        s_cover_buffers[0] == NULL ||
        s_cover_buffers[1] == NULL) {
        err = ESP_ERR_NO_MEM;
        goto fail_queue;
    }

    err = nvs_flash_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "NVS initialization failed: %s", esp_err_to_name(err));
        goto fail_queue;
    }
    err = nimble_port_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "NimBLE initialization failed: %s", esp_err_to_name(err));
        goto fail_queue;
    }

    int rc = ble_att_set_preferred_mtu(COVER_PREFERRED_MTU);
    if (rc != 0) {
        ESP_LOGE(TAG, "preferred ATT MTU setup failed: %d", rc);
        err = ESP_FAIL;
        nimble_port_deinit();
        goto fail_queue;
    }

    ble_hs_cfg.sync_cb = on_sync;
    ble_svc_gap_init();
    ble_svc_gatt_init();

    rc = ble_gatts_count_cfg(services);
    if (rc == 0) {
        rc = ble_gatts_add_svcs(services);
    }
    if (rc == 0) {
        rc = ble_svc_gap_device_name_set(DEVICE_NAME);
    }
    if (rc != 0) {
        ESP_LOGE(TAG, "GATT setup failed: %d", rc);
        err = ESP_FAIL;
        nimble_port_deinit();
        goto fail_queue;
    }

    nimble_port_freertos_init(host_task);
    ESP_LOGI(TAG, "advertising as %s", DEVICE_NAME);
    return ESP_OK;

fail_queue:
    if (s_cover_buffers[0] != NULL) {
        heap_caps_free(s_cover_buffers[0]);
        s_cover_buffers[0] = NULL;
    }
    if (s_cover_buffers[1] != NULL) {
        heap_caps_free(s_cover_buffers[1]);
        s_cover_buffers[1] = NULL;
    }
    if (s_cover_updates != NULL) {
        vQueueDelete(s_cover_updates);
        s_cover_updates = NULL;
    }
    if (s_playback_updates != NULL) {
        vQueueDelete(s_playback_updates);
        s_playback_updates = NULL;
    }
    vQueueDelete(s_updates);
    s_updates = NULL;
    return err;
}

bool music_ble_try_get_update(music_ble_metadata_t *out)
{
    return s_updates != NULL && out != NULL &&
           xQueueReceive(s_updates, out, 0) == pdPASS;
}

bool music_ble_try_get_cover(music_ble_cover_t *out)
{
    if (s_cover_updates == NULL || out == NULL ||
        xQueueReceive(s_cover_updates, out, 0) != pdPASS) {
        return false;
    }

    portENTER_CRITICAL(&s_cover_lock);
    if (out->pixels == s_cover_buffers[0]) {
        s_cover_active_index = 0;
    } else if (out->pixels == s_cover_buffers[1]) {
        s_cover_active_index = 1;
    } else {
        s_cover_active_index = -1;
    }
    s_cover_pending_index = -1;
    portEXIT_CRITICAL(&s_cover_lock);
    return true;
}

bool music_ble_try_get_playback(music_ble_playback_t *out)
{
    return s_playback_updates != NULL && out != NULL &&
           xQueueReceive(s_playback_updates, out, 0) == pdPASS;
}

static bool send_control_value(const uint8_t *value, size_t size)
{
    uint16_t conn_handle;
    bool subscribed;
    portENTER_CRITICAL(&s_control_state_lock);
    conn_handle = s_control_conn_handle;
    subscribed = s_control_subscribed;
    portEXIT_CRITICAL(&s_control_state_lock);
    if (!subscribed || conn_handle == BLE_HS_CONN_HANDLE_NONE ||
        s_control_value_handle == 0) {
        return false;
    }

    struct os_mbuf *packet = ble_hs_mbuf_from_flat(value, size);
    if (packet == NULL) {
        ESP_LOGW(TAG, "control notification allocation failed");
        return false;
    }
    /* NimBLE consumes packet on success and failure; it locks its ATT send
     * path internally, so this may be called from the LVGL task. */
    const int rc = ble_gatts_notify_custom(conn_handle, s_control_value_handle,
                                           packet);
    if (rc != 0) {
        ESP_LOGW(TAG, "control notification failed: command=%u rc=%d",
                 (unsigned)value[1], rc);
        return false;
    }
    ESP_LOGI(TAG, "control notification queued: command=%u", (unsigned)value[1]);
    return true;
}

bool music_ble_send_control(music_ble_control_t command)
{
    if (command < MUSIC_BLE_CONTROL_PREVIOUS || command > MUSIC_BLE_CONTROL_NEXT) {
        return false;
    }
    const uint8_t value[2] = {FRAME_VERSION, (uint8_t)command};
    return send_control_value(value, sizeof(value));
}

bool music_ble_send_volume(uint8_t percent)
{
    if (percent > 100) return false;
    const uint8_t value[3] = {FRAME_VERSION, MUSIC_BLE_CONTROL_VOLUME, percent};
    return send_control_value(value, sizeof(value));
}

bool music_ble_send_seek(uint8_t track_id, uint32_t position_ms)
{
    const uint8_t value[7] = {
        FRAME_VERSION, MUSIC_BLE_CONTROL_SEEK, track_id,
        (uint8_t)position_ms,
        (uint8_t)(position_ms >> 8),
        (uint8_t)(position_ms >> 16),
        (uint8_t)(position_ms >> 24),
    };
    return send_control_value(value, sizeof(value));
}
