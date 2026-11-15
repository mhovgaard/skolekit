// First panel test for the Waveshare 7.3-inch e-Paper HAT (E) on a
// DFRobot FireBeetle 2 ESP32-E (DFR0654-F). Draws six horizontal colour
// bands (black, white, yellow, red, blue, green) without Wi-Fi or a frame
// buffer. Register sequence adapted from Waveshare EPD_7in3e.cpp (MIT); see
// ../firebeetle-cloud/PANEL-LICENSE.txt.
#include <SPI.h>

// HAT wire -> FireBeetle silkscreen (GPIO).
constexpr uint8_t kPinSck = 18;   // CLK  -> SCK  (IO18)
constexpr uint8_t kPinMosi = 23;  // DIN  -> MOSI (IO23)
constexpr uint8_t kPinCs = 13;    // CS   -> D7   (IO13)
constexpr uint8_t kPinDc = 14;    // DC   -> D6   (IO14)
constexpr uint8_t kPinRst = 26;   // RST  -> D3   (IO26)
constexpr uint8_t kPinBusy = 25;  // BUSY -> D2   (IO25)

constexpr uint16_t kWidth = 800;
constexpr uint16_t kHeight = 480;
constexpr uint32_t kBusyTimeoutMs = 60000;
constexpr uint8_t kBandCodes[] = {0x0, 0x1, 0x2, 0x3, 0x5, 0x6};
const char* const kBandNames[] = {"black", "white", "yellow", "red", "blue", "green"};

const SPISettings kSpi(4000000, MSBFIRST, SPI_MODE0);

void sendCommand(uint8_t command) {
  digitalWrite(kPinDc, LOW);
  digitalWrite(kPinCs, LOW);
  SPI.beginTransaction(kSpi);
  SPI.transfer(command);
  SPI.endTransaction();
  digitalWrite(kPinCs, HIGH);
}

void sendData(const uint8_t* data, size_t length) {
  digitalWrite(kPinDc, HIGH);
  digitalWrite(kPinCs, LOW);
  SPI.beginTransaction(kSpi);
  for (size_t i = 0; i < length; ++i) SPI.transfer(data[i]);
  SPI.endTransaction();
  digitalWrite(kPinCs, HIGH);
}

void command(uint8_t cmd, std::initializer_list<uint8_t> data) {
  sendCommand(cmd);
  if (data.size() > 0) sendData(data.begin(), data.size());
}

// The controller holds BUSY low while working.
bool waitIdle(const char* step) {
  const unsigned long start = millis();
  while (digitalRead(kPinBusy) == LOW) {
    if (millis() - start > kBusyTimeoutMs) {
      Serial.printf("  %s: BUSY stayed low for %lu ms (check BUSY/RST/power wiring)\n",
                    step, kBusyTimeoutMs);
      return false;
    }
    delay(10);
  }
  Serial.printf("  %s: %lu ms\n", step, millis() - start);
  return true;
}

void hardReset() {
  digitalWrite(kPinRst, HIGH);
  delay(20);
  digitalWrite(kPinRst, LOW);
  delay(2);
  digitalWrite(kPinRst, HIGH);
  delay(20);
}

bool initPanel() {
  hardReset();
  if (!waitIdle("reset")) return false;
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
  command(0x04, {});
  return waitIdle("power on");
}

bool refresh() {
  command(0x04, {});
  if (!waitIdle("power on")) return false;
  command(0x12, {0x00});
  if (!waitIdle("refresh")) return false;
  command(0x02, {0x00});
  return waitIdle("power off");
}

void sleepPanel() {
  command(0x07, {0xA5});
}

// solid < 0 draws the six colour bands; otherwise fills with that code.
bool drawTest(int solid) {
  Serial.println(solid < 0 ? "Drawing colour bands:" : "Filling panel:");
  if (!initPanel()) return false;
  uint8_t row[kWidth / 2];
  sendCommand(0x10);
  for (uint16_t y = 0; y < kHeight; ++y) {
    const uint8_t code = solid >= 0 ? solid : kBandCodes[y / (kHeight / 6)];
    memset(row, (code << 4) | code, sizeof(row));
    sendData(row, sizeof(row));
  }
  const bool ok = refresh();
  sleepPanel();
  Serial.println(ok ? "Done. Panel is asleep." : "Failed.");
  return ok;
}

void printHelp() {
  Serial.println("Commands (press Enter): bands, white, black, busy, help");
  Serial.print("Expected bands top to bottom:");
  for (const char* name : kBandNames) Serial.printf(" %s", name);
  Serial.println();
}

void handle(const char* cmd) {
  if (strcmp(cmd, "bands") == 0) {
    drawTest(-1);
  } else if (strcmp(cmd, "white") == 0) {
    drawTest(0x1);
  } else if (strcmp(cmd, "black") == 0) {
    drawTest(0x0);
  } else if (strcmp(cmd, "busy") == 0) {
    Serial.printf("BUSY pin reads %s\n", digitalRead(kPinBusy) ? "HIGH (idle)" : "LOW (busy)");
  } else {
    printHelp();
  }
}

void readCommands() {
  static char buffer[16];
  static size_t length = 0;
  while (Serial.available()) {
    const char ch = static_cast<char>(Serial.read());
    if (ch == '\r' || ch == '\n') {
      if (length == 0) continue;
      buffer[length] = '\0';
      handle(buffer);
      length = 0;
    } else if (length < sizeof(buffer) - 1) {
      buffer[length++] = ch;
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  pinMode(kPinCs, OUTPUT);
  pinMode(kPinDc, OUTPUT);
  pinMode(kPinRst, OUTPUT);
  pinMode(kPinBusy, INPUT);
  digitalWrite(kPinCs, HIGH);
  SPI.begin(kPinSck, -1, kPinMosi, kPinCs);
  Serial.println("AG dashboard: 7.3in Spectra 6 panel test started");
  printHelp();
  drawTest(-1);
}

void loop() {
  readCommands();
  delay(5);
}
