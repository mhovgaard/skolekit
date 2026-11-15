#pragma once
#include "battery_level.h"
#include "wake_plan.h"
#include <cstdint>

// Power settings, the wake–sync–draw–sleep cycle record and battery readings.
namespace power {
enum Mode : uint8_t { kManual = 0, kAwake = 1, kSleep = 2 };
enum Wake : uint8_t { kStart = 0, kTimer = 1, kButton = 2 };
// How long a restart or button wake stays awake for the status page in sleep mode.
constexpr uint32_t kMaintenanceMs = 10 * 60 * 1000;

struct Settings {
  uint8_t mode = kManual;
  uint16_t testMinutes = 0;     // Non-zero: wake at this interval instead of the scan times.
  uint16_t scalePermille = 1000; // Battery calibration against a multimeter.
};
// Saved in flash on every wake, so it survives deep sleep and restarts.
struct Record {
  uint32_t wakes = 0, timerWakes = 0, buttonWakes = 0, failures = 0, cycles = 0, sleeps = 0;
  uint32_t awakeMs = 0, totalAwakeMs = 0; // This wake's, and across all sleeps.
  int32_t lateSeconds = 0;       // How far the last timer wake missed its plan.
  int64_t nextChangeAt = 0, nextWakeAt = 0, sleptAt = 0, wokeAt = 0;
  char nextReason[20] = "", outcome[32] = "not_recorded";
  battery::History battery;
};
// The deep-sleep timer's learned drift (see wake_plan.h). Saved apart from the
// record, so adding it kept the existing wake history.
struct Timer {
  int32_t driftPpm = 0;     // How much longer (+) or shorter (−) a timer sleep really lasts.
  int32_t lastPpm = 0;      // What the last trusted timer wake measured.
  uint32_t samples = 0;     // Trusted timer wakes measured.
  int64_t requested = 0;    // Seconds asked of the timer for the current sleep.
  bool sleptSynced = false; // The clock was set from the network when that sleep began.
};

void begin();
Wake wakeCause();
// True once for each press of the IO27 button while awake.
bool buttonPressed();
Settings settings();
Record record();
Timer timer();
bool setMode(uint8_t mode);
bool setTestMinutes(uint16_t minutes);
// Scales readings so the current one matches `measuredMv`; 0 resets the scale.
bool calibrate(uint16_t measuredMv);
uint16_t readBattery(); // Fresh averaged reading in millivolts.
void sampleBattery();   // A fresh reading, also saved in the history.
uint16_t lastBattery();
bool batteryPresent(uint16_t millivolts);
void noteChange(int64_t at);
// The clock now comes from network or server time, not the drifting deep-sleep clock.
void clockSynced();
// Records when this wake started, once the clock is known, and learns the timer's
// drift from a timer wake that began and ended on network time.
void noteClock(int64_t now);
void finishCycle(const char *outcome, bool ok);
wake::Plan nextPlan(int64_t now);
// Saves the record and deep-sleeps until the plan, or for `seconds` with `reason`.
[[noreturn]] void sleep(int64_t now, int64_t seconds = 0, const char *reason = nullptr);
}
