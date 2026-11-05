#include "touch_xpt2046.h"

#include <stddef.h>

#include "driver/gpio.h"
#include "driver/spi_master.h"

#define TOUCH_HOST SPI3_HOST
#define TOUCH_SCLK GPIO_NUM_4
#define TOUCH_MOSI GPIO_NUM_5
#define TOUCH_MISO GPIO_NUM_6
#define TOUCH_CS GPIO_NUM_14
#define TOUCH_IRQ GPIO_NUM_21
#define TOUCH_CLOCK_HZ 2000000
#define TOUCH_SAMPLES 5
#define TOUCH_ADC_MIN 50
#define TOUCH_ADC_MAX 4045

/* XPT2046 command: start, 12-bit differential conversion, power down after
 * the reading so PENIRQ remains available. The LCD's rotation is independent. */
#define TOUCH_READ_X 0xD0
#define TOUCH_READ_Y 0x90

static spi_device_handle_t s_touch;

static bool read_axis(uint8_t command, uint16_t *value)
{
    spi_transaction_t transaction = {0};
    transaction.flags = SPI_TRANS_USE_TXDATA | SPI_TRANS_USE_RXDATA;
    transaction.length = 24;
    transaction.tx_data[0] = command;

    if (spi_device_polling_transmit(s_touch, &transaction) != ESP_OK) {
        return false;
    }

    /* The first bit after the command is null, then come 12 ADC bits. */
    *value = (uint16_t)((((uint16_t)transaction.rx_data[1] << 8) |
                         transaction.rx_data[2]) >> 3);
    return true;
}

static uint16_t median(uint16_t *samples, size_t count)
{
    for (size_t i = 1; i < count; i++) {
        uint16_t key = samples[i];
        size_t j = i;
        while (j > 0 && samples[j - 1] > key) {
            samples[j] = samples[j - 1];
            j--;
        }
        samples[j] = key;
    }
    return samples[count / 2];
}

esp_err_t touch_xpt2046_init(void)
{
    if (s_touch != NULL) {
        return ESP_OK;
    }

    const gpio_config_t irq_config = {
        .pin_bit_mask = 1ULL << TOUCH_IRQ,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t err = gpio_config(&irq_config);
    if (err != ESP_OK) {
        return err;
    }

    const spi_bus_config_t bus_config = {
        .sclk_io_num = TOUCH_SCLK,
        .mosi_io_num = TOUCH_MOSI,
        .miso_io_num = TOUCH_MISO,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
    };
    err = spi_bus_initialize(TOUCH_HOST, &bus_config, SPI_DMA_DISABLED);
    if (err != ESP_OK) {
        return err;
    }

    const spi_device_interface_config_t device_config = {
        .clock_speed_hz = TOUCH_CLOCK_HZ,
        .mode = 0,
        .spics_io_num = TOUCH_CS,
        .queue_size = 1,
    };
    err = spi_bus_add_device(TOUCH_HOST, &device_config, &s_touch);
    if (err != ESP_OK) {
        spi_bus_free(TOUCH_HOST);
    }
    return err;
}

bool touch_xpt2046_irq_active(void)
{
    return s_touch != NULL && gpio_get_level(TOUCH_IRQ) == 0;
}

bool touch_xpt2046_read_raw(uint16_t *raw_x, uint16_t *raw_y)
{
    if (raw_x == NULL || raw_y == NULL || !touch_xpt2046_irq_active()) {
        return false;
    }

    uint16_t x_samples[TOUCH_SAMPLES];
    uint16_t y_samples[TOUCH_SAMPLES];
    size_t valid = 0;
    uint16_t x;
    uint16_t y;

    /* The first reading after switching channels can be noisy. */
    if (!read_axis(TOUCH_READ_X, &x) || !read_axis(TOUCH_READ_Y, &y)) {
        return false;
    }
    for (size_t i = 0; i < TOUCH_SAMPLES && touch_xpt2046_irq_active(); i++) {
        if (!read_axis(TOUCH_READ_X, &x) || !read_axis(TOUCH_READ_Y, &y)) {
            return false;
        }
        if (x >= TOUCH_ADC_MIN && x <= TOUCH_ADC_MAX &&
            y >= TOUCH_ADC_MIN && y <= TOUCH_ADC_MAX) {
            x_samples[valid] = x;
            y_samples[valid] = y;
            valid++;
        }
    }
    if (valid < 3 || !touch_xpt2046_irq_active()) {
        return false;
    }

    *raw_x = median(x_samples, valid);
    *raw_y = median(y_samples, valid);
    return true;
}
