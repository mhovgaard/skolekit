#pragma once
#include <cstddef>
#include <cstdint>

// Waveshare 7.3-inch e-Paper HAT (E), Spectra 6. Wiring and init sequence were
// verified on the delivered panel with firmware/firebeetle-epd-test/.
// Register sequence adapted from Waveshare EPD_7in3e.cpp (MIT); see PANEL-LICENSE.txt.
namespace panel {
void begin();
// Resets and powers the controller, then opens its image RAM for writing.
bool open();
void write(const uint8_t *data, size_t length);
// Shows the written image. Takes about 30 seconds on this panel.
bool refresh(uint32_t &refreshMs);
void sleep();
}
