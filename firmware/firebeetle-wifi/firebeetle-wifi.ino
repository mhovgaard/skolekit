// Standalone Wi-Fi playground for FireBeetle 2 ESP32-E (DFR0654-F).
// USB power only. No home-network credentials, cloud requests, or display wiring.
#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <esp32-hal-rgb-led.h>
#include "page.h"

constexpr uint8_t kLedPin = 2;
constexpr uint8_t kRgbPin = 5;
constexpr uint8_t kButtonPin = 27;
constexpr char kSsid[] = "FireBeetle-Play";
// A demo-network password, not a home Wi-Fi or cloud credential.
constexpr char kPassword[] = "blink1234";
WebServer server(80);
String ledMode = "blink";
String rgbColor = "blue";
bool ledOn = false;
bool wifiReady = false;
uint32_t buttonPresses = 0;

bool setLedMode(const String &mode) {
  if (mode != "on" && mode != "off" && mode != "blink") return false;
  ledMode = mode;
  return true;
}

bool setColor(const String &color) {
  uint8_t red = 0, green = 0, blue = 0;
  if (color == "red") red = 20;
  else if (color == "green") green = 20;
  else if (color == "blue") blue = 20;
  else if (color == "purple") { red = 16; blue = 20; }
  else if (color != "off") return false;
  rgbLedWrite(kRgbPin, red, green, blue);
  rgbColor = color;
  return true;
}

String statusJson() {
  char json[520];
  const uint64_t id = ESP.getEfuseMac();
  snprintf(json, sizeof(json),
           "{\"chip\":\"%s\",\"device_id\":\"%04X%08X\","
           "\"flash_mb\":%lu,\"uptime_ms\":%lu,\"free_heap\":%lu,"
           "\"clients\":%u,\"button_presses\":%lu,\"led_mode\":\"%s\","
           "\"led_on\":%s,\"rgb\":\"%s\",\"ip\":\"192.168.4.1\","
           "\"wifi_ready\":%s}",
           ESP.getChipModel(), static_cast<unsigned int>(id >> 32),
           static_cast<unsigned int>(id),
           static_cast<unsigned long>(ESP.getFlashChipSize() / (1024 * 1024)),
           millis(), static_cast<unsigned long>(ESP.getFreeHeap()),
           static_cast<unsigned int>(WiFi.softAPgetStationNum()),
           static_cast<unsigned long>(buttonPresses), ledMode.c_str(),
           ledOn ? "true" : "false", rgbColor.c_str(), wifiReady ? "true" : "false");
  return String(json);
}

void updateLed() {
  ledOn = ledMode == "on" || (ledMode == "blink" && millis() % 2000 < 1000);
  digitalWrite(kLedPin, ledOn ? HIGH : LOW);
}

void sendStatus() {
  updateLed();
  server.sendHeader("Cache-Control", "no-store");
  server.send(200, "application/json", statusJson());
}

void printHelp() {
  Serial.println("Commands: help, info, status, wifi, led on/off/blink, rgb red/green/blue/purple/off");
}

void printWifi() {
  Serial.printf("Wi-Fi: %s\nNetwork: %s\nPassword: %s\nPage: http://192.168.4.1/\nClients: %u\n",
                wifiReady ? "access point running" : "FAILED", kSsid, kPassword,
                static_cast<unsigned int>(WiFi.softAPgetStationNum()));
}

void runCommand(const String &command) {
  if (command == "help") printHelp();
  else if (command == "wifi") printWifi();
  else if (command == "info") {
    Serial.printf("FireBeetle 2 ESP32-E | %s | %u cores | %lu MHz | %lu bytes flash\n",
                  ESP.getChipModel(), ESP.getChipCores(),
                  static_cast<unsigned long>(ESP.getCpuFreqMHz()),
                  static_cast<unsigned long>(ESP.getFlashChipSize()));
    Serial.println(statusJson());
  } else if (command == "status") Serial.println(statusJson());
  else if (command.startsWith("led ") && setLedMode(command.substring(4))) {
    updateLed();
    Serial.println(statusJson());
  } else if (command.startsWith("rgb ") && setColor(command.substring(4))) {
    Serial.println(statusJson());
  } else { Serial.println("Unknown command."); printHelp(); }
}

void readCommands() {
  static char command[40];
  static size_t length = 0;
  static bool overflow = false;
  while (Serial.available()) {
    const char ch = static_cast<char>(Serial.read());
    if (ch == '\r' || ch == '\n') {
      if (length == 0 && !overflow) continue;
      command[length] = '\0';
      if (overflow) Serial.println("Command too long.");
      else runCommand(String(command));
      length = 0;
      overflow = false;
    } else if (length < sizeof(command) - 1) command[length++] = ch;
    else overflow = true;
  }
}

void readButton() {
  static bool previous = HIGH, stable = HIGH;
  static uint32_t changedAt = 0;
  const bool reading = digitalRead(kButtonPin);
  const uint32_t now = millis();
  if (reading != previous) { previous = reading; changedAt = now; }
  if (now - changedAt >= 30 && reading != stable) {
    stable = reading;
    if (stable == LOW) {
      ++buttonPresses;
      if (rgbColor == "blue") setColor("purple");
      else if (rgbColor == "purple") setColor("red");
      else if (rgbColor == "red") setColor("green");
      else setColor("blue");
      Serial.printf("Button 27 pressed: colour %s, total %lu\n",
                    rgbColor.c_str(), static_cast<unsigned long>(buttonPresses));
    }
  }
}

void setup() {
  pinMode(kLedPin, OUTPUT);
  pinMode(kButtonPin, INPUT_PULLUP);
  Serial.begin(115200);
  delay(1000);
  Serial.println("\nFireBeetle Wi-Fi playground starting");
  setColor("blue");
  // AP-only mode does not join or remember the household Wi-Fi network.
  WiFi.mode(WIFI_AP);
  const IPAddress ip(192, 168, 4, 1), mask(255, 255, 255, 0);
  wifiReady = WiFi.softAPConfig(ip, ip, mask) && WiFi.softAP(kSsid, kPassword);
  server.on("/", HTTP_GET, []() {
    server.sendHeader("Cache-Control", "no-store");
    server.send_P(200, "text/html; charset=utf-8", kPlaygroundPage);
  });
  server.on("/api/status", HTTP_GET, sendStatus);
  server.on("/api/led", HTTP_POST, []() {
    if (!setLedMode(server.arg("mode"))) {
      server.send(400, "application/json", "{\"error\":\"Use on, off, or blink\"}");
      return;
    }
    sendStatus();
  });
  server.on("/api/rgb", HTTP_POST, []() {
    if (!setColor(server.arg("color"))) {
      server.send(400, "application/json", "{\"error\":\"Unknown colour\"}");
      return;
    }
    sendStatus();
  });
  server.on("/favicon.ico", HTTP_GET, []() { server.send(204); });
  server.onNotFound([]() { server.send(404, "text/plain", "Not found"); });
  if (wifiReady) server.begin();
  printWifi();
  printHelp();
}

void loop() {
  static uint32_t lastHeartbeat = 0;
  if (wifiReady) server.handleClient();
  readCommands();
  readButton();
  updateLed();
  if (millis() - lastHeartbeat >= 10000) {
    lastHeartbeat = millis();
    Serial.printf("Wi-Fi playground alive: uptime %lu ms, clients %u, heap %lu\n",
                  millis(), static_cast<unsigned int>(WiFi.softAPgetStationNum()),
                  static_cast<unsigned long>(ESP.getFreeHeap()));
  }
  delay(2);
}
