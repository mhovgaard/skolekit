#include "battery_level.h"
#include <cassert>
#include <cstring>

int main() {
  using battery::percent;
  assert(percent(0) == 0 && percent(3300) == 0);
  assert(percent(3900) == 52 && percent(3875) == 47);
  assert(percent(4200) == 100 && percent(4350) == 100);

  battery::History history;
  assert(!strcmp(battery::trend(history), "unknown"));
  history.add(1000, 3900);
  assert(!strcmp(battery::trend(history), "unknown"));
  history.add(1000 + 3600, 3950);
  assert(!strcmp(battery::trend(history), "rising"));
  uint32_t since;
  assert(history.change(6 * 3600, since) == 50 && since == 1000);
  history.add(1000 + 7200, 3905);
  assert(!strcmp(battery::trend(history), "steady"));
  history.add(1000 + 8 * 3600, 3850); // The oldest sample is now outside six hours.
  assert(history.change(6 * 3600, since) == -55 && since == 1000 + 7200);
  assert(!strcmp(battery::trend(history), "falling"));
  for (uint32_t i = 0; i < 30; ++i) history.add(100000 + i * 60, 4000 + i);
  assert(history.count == battery::kSamples && history.samples[0].millivolts == 4006);
}
