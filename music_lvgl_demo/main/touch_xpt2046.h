#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

/* The touch controller has its own SPI3 bus; the LCD remains on SPI2. */
esp_err_t touch_xpt2046_init(void);

/* Returns true only while the pen IRQ is low and valid 12-bit ADC values
 * were read. These are uncalibrated controller coordinates, not LVGL pixels. */
bool touch_xpt2046_read_raw(uint16_t *raw_x, uint16_t *raw_y);

/* Useful for distinguishing a missing touch signal from invalid ADC data. */
bool touch_xpt2046_irq_active(void);
