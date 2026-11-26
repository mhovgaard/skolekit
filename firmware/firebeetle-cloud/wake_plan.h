#pragma once
#include "friendly_status.h"
#include <cstddef>
#include <cstdint>

// When to wake next. Pure, so firmware/tests checks it on the Mac.
namespace wake {
struct Check { uint16_t minute; uint8_t weekdays; }; // Copenhagen minute of day; bit 0 = Monday.
// Half an hour after the daily cloud scan starts at 05:30 (infra/gcp/create-schedules.sh,
// a retry included), when its new board has been published.
constexpr Check kChecks[] = {{6 * 60, 0x7F}};
constexpr int64_t kChangeMargin = 30;   // Seconds after the server's next frame starts.
// A board change this close to the check fetches the newest board itself: one wake, one draw.
constexpr int64_t kMergeWindow = 20 * 60;
constexpr int64_t kMinimumSleep = 60;
constexpr int64_t kMaximumSleep = 12 * 3600;

struct Plan { int64_t at; const char *reason; };

// The first scheduled check strictly after `now` (UTC seconds).
inline int64_t nextCheck(int64_t now) {
  const int offset = friendly::copenhagenOffset(now);
  const int64_t today = (now + offset) / 86400 * 86400; // Local midnight, in local seconds.
  int64_t best = 0;
  for (int day = 0; day < 8; ++day) {
    const int64_t midnight = today + day * 86400;
    const int weekday = static_cast<int>((midnight / 86400 + 3) % 7); // 1970-01-01 was a Thursday.
    for (const Check &check : kChecks) {
      if (!(check.weekdays >> weekday & 1)) continue;
      const int64_t local = midnight + check.minute * 60;
      const int64_t at = local - friendly::copenhagenOffset(local - offset);
      if (at > now && (!best || at < best)) best = at;
    }
    if (best) return best;
  }
  return now + kMaximumSleep;
}

// 5, 10, 20, 40, then 60 minutes after consecutive failed wakes.
inline int64_t retryDelay(uint32_t failures) {
  return failures >= 5 ? 3600 : 300 << (failures - 1);
}

// The earliest useful wake: the next frame change, the next published scan,
// a retry after failures, or the test interval instead of the scan times.
inline Plan plan(int64_t now, int64_t nextChange, uint32_t testMinutes, uint32_t failures) {
  Plan result{now + kMaximumSleep, "longest_sleep"};
  auto consider = [&](int64_t at, const char *reason) {
    if (at > now && at < result.at) result = {at, reason};
  };
  if (testMinutes) consider(now + static_cast<int64_t>(testMinutes) * 60, "test_interval");
  else {
    const int64_t check = nextCheck(now);
    const bool merged = nextChange > now && check > nextChange - kMergeWindow && check < nextChange + kMergeWindow;
    if (!merged) consider(check, "scan_published");
  }
  if (nextChange > 0) consider(nextChange + kChangeMargin, "board_changes");
  if (failures) consider(now + retryDelay(failures), "retry");
  if (result.at < now + kMinimumSleep) result.at = now + kMinimumSleep;
  return result;
}

// The deep-sleep timer runs on the chip's internal RC clock, which drifts by a few
// percent; on 3 and 4 November 2026 this board woke 1.6–2.3% early. The clock kept
// in deep sleep drifts with it, so only network or server time shows the error.
// Each trusted timer wake measures it, and the next sleep is stretched to match.
constexpr int64_t kDriftLearnMinimum = 30 * 60; // Shorter sleeps measure the drift too coarsely.
constexpr int32_t kDriftLimitPpm = 50000;       // Beyond ±5% it is a clock error, not drift.
// Aim this much of the sleep late: an early wake finds no new board and costs a second wake.
constexpr int32_t kLateMarginPpm = 5000;

// Seconds to ask of the timer so a sleep of `seconds` real seconds ends slightly late.
// `driftPpm` is how much longer (+) or shorter (−) a timer sleep really lasts.
inline int64_t timerSeconds(int64_t seconds, int32_t driftPpm) {
  const int64_t rate = 1000000 + driftPpm;
  return (seconds * (1000000 + kLateMarginPpm) + rate / 2) / rate;
}

// The drift one timer wake shows: `requested` seconds asked, `actual` real seconds slept.
inline bool measureDrift(int64_t requested, int64_t actual, int32_t &ppm) {
  if (requested < kDriftLearnMinimum || actual <= 0) return false;
  const int64_t value = (actual - requested) * 1000000 / requested;
  if (value < -kDriftLimitPpm || value > kDriftLimitPpm) return false;
  ppm = static_cast<int32_t>(value);
  return true;
}

// The first measurement is taken as it is; each later one moves the estimate a quarter
// of the way, so one unusual night does not swing the next wake.
inline int32_t learnDrift(int32_t current, uint32_t samples, int32_t measured) {
  return samples ? current + (measured - current) / 4 : measured;
}
}
