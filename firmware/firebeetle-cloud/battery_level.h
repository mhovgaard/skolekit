#pragma once
#include <cstdint>

// Battery estimates from the FireBeetle's IO34 divider. Pure, so firmware/tests
// checks them on the Mac. The board cannot report charging directly.
namespace battery {
constexpr uint16_t kAbsentBelowMv = 2500; // A connected Li-ion cell is never this low.
constexpr uint16_t kLowMv = 3450;         // Below this, skip Wi-Fi and the panel refresh.
constexpr uint8_t kSamples = 24;

struct Sample { uint32_t at; uint16_t millivolts; }; // UTC seconds; 0 = unknown time.

// Resting single-cell Li-ion voltage to remaining charge: a rough estimate.
inline int percent(uint16_t millivolts) {
  static const uint16_t mv[] = {3300, 3600, 3700, 3750, 3800, 3850, 3900, 3950, 4000, 4100, 4200};
  static const uint8_t pct[] = {0, 5, 12, 20, 30, 42, 52, 62, 72, 88, 100};
  if (millivolts <= mv[0]) return 0;
  for (int i = 1; i < 11; ++i)
    if (millivolts <= mv[i]) return pct[i - 1] + (pct[i] - pct[i - 1]) * (millivolts - mv[i - 1]) / (mv[i] - mv[i - 1]);
  return 100;
}

// Recent samples in time order; `count` of them, the newest last.
struct History {
  Sample samples[kSamples];
  uint8_t count = 0;
  void add(uint32_t at, uint16_t millivolts) {
    if (count == kSamples) { for (int i = 1; i < kSamples; ++i) samples[i - 1] = samples[i]; --count; }
    samples[count++] = {at, millivolts};
  }
  // Millivolts gained (negative: lost) across samples taken within `window`
  // seconds of the newest one, and that span's first sample time.
  int change(uint32_t window, uint32_t &since) const {
    since = 0;
    if (count < 2 || !samples[count - 1].at) return 0;
    const Sample &last = samples[count - 1];
    for (int i = 0; i < count - 1; ++i)
      if (samples[i].at && last.at - samples[i].at <= window) { since = samples[i].at; return last.millivolts - samples[i].millivolts; }
    return 0;
  }
};

// "rising" (probably charging), "falling", "steady" or "unknown".
inline const char *trend(const History &history) {
  uint32_t since;
  int delta = history.change(6 * 3600, since);
  if (!since) return "unknown";
  return delta >= 30 ? "rising" : delta <= -30 ? "falling" : "steady";
}
}
