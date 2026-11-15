#include "power.h"
#include <Arduino.h>
#include <Preferences.h>
#include <WiFi.h>
#include <atomic>
#include <driver/rtc_io.h>
#include <esp_sleep.h>

namespace power {
namespace {
constexpr uint8_t kBatteryPin = 34;        // IO34/A2: the battery through the board's 1:2 divider.
constexpr gpio_num_t kButtonPin = GPIO_NUM_27; // On-board user button, LOW while pressed.
Preferences store;
Settings current;
Record saved;
Timer drift;
bool synced = false; // This wake's clock came from network or server time.
Wake cause = kStart;
uint16_t latest = 0;
SemaphoreHandle_t mutex;
// Latched by interrupt, so a press during a blocking sync or draw is not lost.
std::atomic<bool> pressed{false};
void ARDUINO_ISR_ATTR onButton() { pressed = true; }
void lock() { xSemaphoreTake(mutex, portMAX_DELAY); }
void unlock() { xSemaphoreGive(mutex); }
bool clockKnown(int64_t now) { return now >= 1767225600; } // 2026-01-01
void persist() { Record copy = record(); store.putBytes("record-v1", &copy, sizeof(copy)); }
void persistSettings() { store.putBytes("settings-v1", &current, sizeof(current)); }
void persistTimer() { Timer copy = timer(); store.putBytes("timer-v1", &copy, sizeof(copy)); }
// Averaged millivolts at the battery terminal, before calibration.
uint32_t rawBattery() {
  uint32_t sum = 0;
  for (int i = 0; i < 16; ++i) sum += analogReadMilliVolts(kBatteryPin);
  return sum / 16 * 2;
}
} // namespace

void begin() {
  mutex = xSemaphoreCreateMutex();
  store.begin("ag-power", false);
  if (store.getBytesLength("settings-v1") == sizeof(current)) store.getBytes("settings-v1", &current, sizeof(current));
  if (store.getBytesLength("record-v1") == sizeof(saved)) store.getBytes("record-v1", &saved, sizeof(saved));
  if (store.getBytesLength("timer-v1") == sizeof(drift)) store.getBytes("timer-v1", &drift, sizeof(drift));
  switch (esp_sleep_get_wakeup_cause()) {
    case ESP_SLEEP_WAKEUP_TIMER: cause = kTimer; break;
    case ESP_SLEEP_WAKEUP_EXT0: cause = kButton; break;
    default: cause = kStart;
  }
  // Sleep left the button on the RTC; take it back so presses are seen while awake too.
  rtc_gpio_deinit(kButtonPin);
  pinMode(kButtonPin, INPUT_PULLUP);
  attachInterrupt(kButtonPin, onButton, FALLING);
  ++saved.wakes;
  if (cause == kTimer) ++saved.timerWakes;
  if (cause == kButton) ++saved.buttonWakes;
  analogSetPinAttenuation(kBatteryPin, ADC_11db);
  // Read before Wi-Fi starts, while the cell is at rest.
  uint16_t millivolts = readBattery();
  int64_t now = time(nullptr);
  saved.battery.add(clockKnown(now) ? static_cast<uint32_t>(now) : 0, millivolts);
  persist();
}
Wake wakeCause() { return cause; }
bool buttonPressed() {
  static uint32_t lastMs = 0;
  if (!pressed.exchange(false)) return false;
  if (lastMs && millis() - lastMs < 300) return false; // Contact bounce.
  lastMs = millis();
  return true;
}
Settings settings() { lock(); Settings copy = current; unlock(); return copy; }
Record record() { lock(); Record copy = saved; unlock(); return copy; }
Timer timer() { lock(); Timer copy = drift; unlock(); return copy; }
bool setMode(uint8_t mode) {
  if (mode > kSleep) return false;
  lock(); current.mode = mode; unlock(); persistSettings();
  return true;
}
bool setTestMinutes(uint16_t minutes) {
  if (minutes > 720) return false;
  lock(); current.testMinutes = minutes; unlock(); persistSettings();
  return true;
}
bool calibrate(uint16_t measuredMv) {
  uint32_t raw = rawBattery();
  uint32_t scale = measuredMv ? measuredMv * 1000UL / (raw ? raw : 1) : 1000;
  if (measuredMv && (raw < battery::kAbsentBelowMv || scale < 800 || scale > 1200)) return false;
  lock(); current.scalePermille = scale; unlock(); persistSettings();
  readBattery();
  return true;
}
uint16_t readBattery() {
  uint32_t millivolts = rawBattery() * settings().scalePermille / 1000;
  lock(); latest = millivolts; unlock();
  return millivolts;
}
uint16_t lastBattery() { lock(); uint16_t copy = latest; unlock(); return copy; }
bool batteryPresent(uint16_t millivolts) { return millivolts >= battery::kAbsentBelowMv; }
void noteChange(int64_t at) { lock(); saved.nextChangeAt = at; unlock(); }
void clockSynced() { lock(); synced = true; unlock(); }
void noteClock(int64_t now) {
  if (!clockKnown(now)) return;
  lock();
  saved.wokeAt = now - millis() / 1000;
  if (cause == kTimer && saved.nextWakeAt) saved.lateSeconds = saved.wokeAt - saved.nextWakeAt;
  // Both ends on network time: the real length of the sleep against what the timer was asked.
  int32_t measured = 0;
  const bool learned = cause == kTimer && synced && drift.sleptSynced && saved.sleptAt
    && wake::measureDrift(drift.requested, saved.wokeAt - saved.sleptAt, measured);
  if (learned) {
    drift.driftPpm = wake::learnDrift(drift.driftPpm, drift.samples, measured);
    drift.lastPpm = measured;
    ++drift.samples;
  }
  // The reading taken at start had no time before the clock was set.
  if (saved.battery.count && !saved.battery.samples[saved.battery.count - 1].at)
    saved.battery.samples[saved.battery.count - 1].at = saved.wokeAt;
  unlock();
  persist();
  if (learned) persistTimer();
}
void sampleBattery() {
  uint16_t millivolts = readBattery();
  int64_t now = time(nullptr);
  lock(); saved.battery.add(clockKnown(now) ? static_cast<uint32_t>(now) : 0, millivolts); unlock();
  persist();
}
void finishCycle(const char *outcome, bool ok) {
  lock();
  ++saved.cycles;
  saved.failures = ok ? 0 : saved.failures + 1;
  strlcpy(saved.outcome, outcome, sizeof(saved.outcome));
  unlock();
  persist();
}
wake::Plan nextPlan(int64_t now) {
  Record copy = record();
  return wake::plan(now, copy.nextChangeAt, settings().testMinutes, copy.failures);
}
void sleep(int64_t now, int64_t seconds, const char *reason) {
  wake::Plan plan = seconds ? wake::Plan{now + seconds, reason}
    : clockKnown(now) ? nextPlan(now)
    : wake::Plan{now + wake::retryDelay(record().failures ? record().failures : 1), "clock_unknown"};
  const uint32_t awake = millis();
  lock();
  // Stretch or shorten the sleep by the timer's learned drift, so it wakes on time.
  const int64_t timerSeconds = wake::timerSeconds(plan.at - now, drift.driftPpm);
  saved.nextWakeAt = plan.at; saved.sleptAt = now;
  strlcpy(saved.nextReason, plan.reason, sizeof(saved.nextReason));
  saved.awakeMs = awake; saved.totalAwakeMs += awake; ++saved.sleeps;
  drift.requested = timerSeconds; drift.sleptSynced = synced;
  unlock();
  persist();
  persistTimer();
  Serial.printf("{\"sleep_seconds\":%lld,\"timer_seconds\":%lld,\"wake_reason\":\"%s\",\"awake_ms\":%lu}\n",
                static_cast<long long>(plan.at - now), static_cast<long long>(timerSeconds), plan.reason,
                static_cast<unsigned long>(awake));
  Serial.flush();
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  esp_sleep_enable_timer_wakeup(static_cast<uint64_t>(timerSeconds) * 1000000ULL);
  detachInterrupt(kButtonPin);
  rtc_gpio_pullup_en(kButtonPin);
  rtc_gpio_pulldown_dis(kButtonPin);
  esp_sleep_enable_ext0_wakeup(kButtonPin, 0);
  esp_deep_sleep_start();
}
}
