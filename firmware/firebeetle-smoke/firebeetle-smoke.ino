// First USB test for DFRobot FireBeetle 2 ESP32-E (DFR0654-F).
// Uses only the onboard LED; leave the display and battery disconnected.
constexpr uint8_t kLedPin = 2;  // GPIO2 / D9, not the RGB LED on GPIO5.

void printHelp() {
  Serial.println("Commands (press Enter): help, info, status");
}

void printInfo() {
  const uint64_t deviceId = ESP.getEfuseMac();
  Serial.println("Board target: DFRobot FireBeetle 2 ESP32-E (DFR0654-F)");
  Serial.printf("Chip: %s, revision %u, cores %u\n", ESP.getChipModel(),
                ESP.getChipRevision(), ESP.getChipCores());
  Serial.printf("Device ID (eFuse): %04X%08X\n",
                static_cast<unsigned int>(deviceId >> 32),
                static_cast<unsigned int>(deviceId));
  Serial.printf("Flash: %lu bytes, CPU: %lu MHz\n",
                static_cast<unsigned long>(ESP.getFlashChipSize()),
                static_cast<unsigned long>(ESP.getCpuFreqMHz()));
  Serial.printf("ESP-IDF: %s\n", ESP.getSdkVersion());
}

void printStatus() {
  Serial.printf("Status: running, uptime %lu ms, free heap %lu bytes\n",
                millis(), static_cast<unsigned long>(ESP.getFreeHeap()));
  Serial.println("Display, Wi-Fi and battery sensing are not enabled in this test.");
}

void readCommands() {
  static char command[16];
  static size_t length = 0;
  static bool overflow = false;
  while (Serial.available()) {
    const char ch = static_cast<char>(Serial.read());
    if (ch == '\r' || ch == '\n') {
      if (length == 0 && !overflow) continue;
      command[length] = '\0';
      if (overflow) {
        Serial.println("Command too long.");
      } else if (strcmp(command, "help") == 0) {
        printHelp();
      } else if (strcmp(command, "info") == 0) {
        printInfo();
      } else if (strcmp(command, "status") == 0) {
        printStatus();
      } else {
        Serial.println("Unknown command.");
        printHelp();
      }
      length = 0;
      overflow = false;
    } else if (length < sizeof(command) - 1) {
      command[length++] = ch;
    } else {
      overflow = true;
    }
  }
}

void setup() {
  pinMode(kLedPin, OUTPUT);
  digitalWrite(kLedPin, LOW);
  Serial.begin(115200);
  delay(1000);
  Serial.println("AG dashboard: FireBeetle ESP32-E USB test started");
  printHelp();
}

void loop() {
  static unsigned long lastHeartbeat = 0;
  const unsigned long now = millis();
  digitalWrite(kLedPin, now % 2000 < 250 ? HIGH : LOW);
  readCommands();
  if (now - lastHeartbeat >= 10000) {
    lastHeartbeat = now;
    Serial.printf("FireBeetle ESP32-E alive: uptime %lu ms\n", now);
  }
  delay(5);
}
