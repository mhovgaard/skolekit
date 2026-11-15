#include "friendly_status.h"
#include <cassert>
#include <string>

int main() {
  using namespace friendly;
  assert(duration(0) == "0 seconds");
  assert(duration(1000) == "1 second");
  assert(duration(90061000) == "1 day 1 hour 1 minute 1 second");
  assert(seconds(11206) == "11.2 seconds");
  assert(seconds(10) == "Less than 0.1 seconds");
  assert(memory(138) == "138 bytes");
  assert(memory(171032) == "171.0 kB");
  assert(speed(192000, 6000) == "32.0 kB/s");
  assert(speed(0, 6000) == "Not recorded");
  assert(speed(192000, 0) == "Not recorded");
  assert(percent(3, 4) == "75%");
  assert(percent(0, 0) == "Not recorded");
  assert(count(1, "check") == "1 check");
  assert(count(3, "start") == "3 starts");
  assert(timerDrift(-19000, 0) == "timer drift not measured yet");
  assert(timerDrift(-19000, 3) == "timer runs 1.9% fast, corrected");
  assert(timerDrift(12345, 1) == "timer runs 1.2% slow, corrected");
  assert(timerDrift(-499, 4) == "timer on time");
  assert(signal(-61, true) == "Good (-61 dBm)");
  assert(signal(0, false) == "Not connected");
  assert(date("") == "Not recorded");
  assert(date("2026-02-29T12:00:00Z") == "Not recorded");
  assert(date("2026-10-23T21:27:32Z") == "23 Oct 2026, 23:27:32 CEST");
  assert(date("2026-10-23T23:27:32.123+02:00") == "23 Oct 2026, 23:27:32 CEST");
  assert(date("2026-10-25T00:59:59Z") == "25 Oct 2026, 02:59:59 CEST");
  assert(date("2026-10-25T01:00:00Z") == "25 Oct 2026, 02:00:00 CET");
  assert(date("2026-03-29T00:59:59Z") == "29 Mar 2026, 01:59:59 CET");
  assert(date("2026-03-29T01:00:00Z") == "29 Mar 2026, 03:00:00 CEST");
  assert(expired("1970-01-01T00:01:00Z", 60));
  assert(!expired("1970-01-01T00:01:00Z", 59));
  assert(http(200, true) == "HTTP 200 (OK)");
  assert(http(0, true) == "No HTTP response received");
  assert(http(0, false) == "Not checked yet");
  assert(std::string(outcome("connected_no_publication")) == "GCP login succeeded; no current board is available");
  assert(std::string(outcome("credentials_rejected")).find("rejected") != std::string::npos);
}
