#include "panel.h"
#include <Arduino.h>
#include <SPI.h>
#include <initializer_list>

namespace panel {
namespace {
// HAT wire -> FireBeetle label: DIN -> MOSI, CLK -> SCK, CS -> 13/D7,
// DC -> 14/D6, RST -> 26/D3, BUSY -> 25/D2.
constexpr uint8_t kSck = 18, kMosi = 23, kCs = 13, kDc = 14, kRst = 26, kBusy = 25;
constexpr uint32_t kBusyTimeoutMs = 60000;
const SPISettings kSpi(4000000, MSBFIRST, SPI_MODE0);

void transfer(bool data, const uint8_t *bytes, size_t length) {
  digitalWrite(kDc, data ? HIGH : LOW);
  digitalWrite(kCs, LOW);
  SPI.beginTransaction(kSpi);
  for (size_t i = 0; i < length; ++i) SPI.transfer(bytes[i]);
  SPI.endTransaction();
  digitalWrite(kCs, HIGH);
}
void command(uint8_t cmd, std::initializer_list<uint8_t> data = {}) {
  transfer(false, &cmd, 1);
  if (data.size()) transfer(true, data.begin(), data.size());
}
// The controller holds BUSY low while working.
bool waitIdle() {
  const uint32_t start = millis();
  while (digitalRead(kBusy) == LOW) {
    if (millis() - start > kBusyTimeoutMs) return false;
    delay(10);
  }
  return true;
}
} // namespace

void begin() {
  pinMode(kCs, OUTPUT); pinMode(kDc, OUTPUT); pinMode(kRst, OUTPUT); pinMode(kBusy, INPUT);
  digitalWrite(kCs, HIGH); digitalWrite(kRst, HIGH);
  SPI.begin(kSck, -1, kMosi, kCs);
}
bool open() {
  digitalWrite(kRst, HIGH); delay(20);
  digitalWrite(kRst, LOW); delay(2);
  digitalWrite(kRst, HIGH); delay(20);
  if (!waitIdle()) return false;
  delay(30);
  command(0xAA, {0x49, 0x55, 0x20, 0x08, 0x09, 0x18});
  command(0x01, {0x3F});
  command(0x00, {0x5F, 0x69});
  command(0x03, {0x00, 0x54, 0x00, 0x44});
  command(0x05, {0x40, 0x1F, 0x1F, 0x2C});
  command(0x06, {0x6F, 0x1F, 0x17, 0x49});
  command(0x08, {0x6F, 0x1F, 0x1F, 0x22});
  command(0x30, {0x03});
  command(0x50, {0x3F});
  command(0x60, {0x02, 0x00});
  command(0x61, {0x03, 0x20, 0x01, 0xE0});
  command(0x84, {0x01});
  command(0xE3, {0x2F});
  command(0x04);
  if (!waitIdle()) return false;
  command(0x10);
  return true;
}
void write(const uint8_t *data, size_t length) { transfer(true, data, length); }
bool refresh(uint32_t &refreshMs) {
  command(0x04);
  if (!waitIdle()) return false;
  const uint32_t start = millis();
  command(0x12, {0x00});
  if (!waitIdle()) return false;
  refreshMs = millis() - start;
  command(0x02, {0x00});
  return waitIdle();
}
void sleep() { command(0x07, {0xA5}); }
}
