#include "wake_plan.h"
#include <cassert>
#include <cstring>

static int64_t at(const char *iso) { return friendly::timestamp(iso); }

int main() {
  using wake::nextCheck; using wake::plan;
  assert(nextCheck(at("2026-11-02T05:00:00+01:00")) == at("2026-11-02T05:52:00+01:00")); // Monday morning.
  assert(nextCheck(at("2026-11-02T05:52:00+01:00")) == at("2026-11-02T12:37:00+01:00"));
  assert(nextCheck(at("2026-11-02T20:00:00+01:00")) == at("2026-11-03T05:52:00+01:00"));
  assert(nextCheck(at("2026-11-06T20:00:00+01:00")) == at("2026-11-07T12:37:00+01:00")); // No Saturday 05:52.
  assert(nextCheck(at("2026-11-07T13:00:00+01:00")) == at("2026-11-07T19:37:00+01:00"));
  assert(nextCheck(at("2026-11-08T20:00:00+01:00")) == at("2026-11-09T05:52:00+01:00")); // Sunday night to Monday.
  assert(nextCheck(at("2027-03-27T20:00:00+01:00")) == at("2027-03-28T12:37:00+02:00")); // Into summer time.
  assert(nextCheck(at("2026-10-24T20:00:00+02:00")) == at("2026-10-25T12:37:00+01:00")); // Into winter time.

  const int64_t monday = at("2026-11-02T13:00:00+01:00");
  wake::Plan p = plan(monday, at("2026-11-02T15:00:00+01:00"), 0, 0);
  assert(p.at == at("2026-11-02T15:00:30+01:00") && !strcmp(p.reason, "board_changes"));
  p = plan(monday, 0, 0, 0);
  assert(p.at == at("2026-11-02T19:37:00+01:00") && !strcmp(p.reason, "scan_published"));
  p = plan(monday, at("2026-11-02T12:00:00+01:00"), 0, 0); // A past change is ignored.
  assert(!strcmp(p.reason, "scan_published"));
  p = plan(monday, at("2026-11-02T15:00:00+01:00"), 3, 0);
  assert(p.at == monday + 180 && !strcmp(p.reason, "test_interval"));
  p = plan(monday, monday + 60, 3, 0);
  assert(p.at == monday + 90 && !strcmp(p.reason, "board_changes"));
  p = plan(monday, 0, 0, 1);
  assert(p.at == monday + 300 && !strcmp(p.reason, "retry"));
  assert(wake::retryDelay(3) == 1200 && wake::retryDelay(9) == 3600);
  p = plan(monday, monday + 5, 0, 0); // Never sooner than a minute.
  assert(p.at == monday + 60);
  p = plan(at("2026-11-02T19:36:00+01:00"), 0, 0, 0);
  assert(p.at == at("2026-11-02T19:37:00+01:00"));

  // Timer drift, from the board's wakes on 3–4 November 2026 (Copenhagen time).
  using wake::learnDrift; using wake::measureDrift; using wake::timerSeconds;
  int32_t ppm = 0;
  // Asked 05:52:39 to 12:37:00 (24,261 s), it woke 6 min 26 s early: 1.59% fast.
  assert(measureDrift(24261, at("2026-11-04T12:30:34+01:00") - at("2026-11-04T05:52:39+01:00"), ppm));
  assert(ppm == -15910);
  // Asked 12:37:04 to 15:00:30 (8,606 s), it woke 197 s early: 2.29% fast.
  assert(measureDrift(8606, 8409, ppm) && ppm == -22891);
  assert(measureDrift(3600, 3600, ppm) && ppm == 0);
  assert(measureDrift(3600, 3672, ppm) && ppm == 20000); // A slow timer measures positive.
  assert(!measureDrift(179, 177, ppm));    // Too short to measure: one second is 0.6%.
  assert(!measureDrift(3600, 3000, ppm));  // 17% is a clock error, not drift.
  assert(!measureDrift(3600, 0, ppm));

  // The first measurement is taken as it is; later ones move it a quarter of the way.
  assert(learnDrift(0, 0, -15910) == -15910);
  assert(learnDrift(-15910, 1, -22891) == -17655);
  assert(learnDrift(-20000, 5, -20000) == -20000);

  // Without a measurement, sleeps are aimed 0.5% late; 6 hours ask 108 s more.
  assert(timerSeconds(21600, 0) == 21708);
  // A timer 1.9% fast is asked for 2.4% more, so a 6-hour sleep ends about 108 s late.
  const int64_t asked = timerSeconds(21600, -19000);
  assert(asked == 22128);
  const int64_t slept = asked * (1000000 - 19000) / 1000000;
  assert(slept >= 21600 + 100 && slept <= 21600 + 110);
  // Even the fastest night measured (2.29%) against a 1.9% estimate wakes on time.
  assert(asked * (1000000 - 22891) / 1000000 >= 21600 - 5);
  assert(timerSeconds(60, -19000) == 61); // The minimum sleep barely changes.
  assert(timerSeconds(21600, 20000) == 21282); // A slow timer is asked for less.
}
