#include "friendly_status.h"
#include <cstdio>
#include <cstring>
#include <ctime>

namespace friendly {
std::string count(uint64_t value, const char *noun) {
  return std::to_string(value) + " " + noun + (value == 1 ? "" : "s");
}
std::string timerDrift(int32_t ppm, uint32_t samples) {
  if (!samples) return "timer drift not measured yet";
  // A timer sleep that really lasts less (negative) means the timer runs fast.
  const int32_t size = ppm < 0 ? -ppm : ppm;
  if (size < 500) return "timer on time";
  char text[48];
  snprintf(text, sizeof(text), "timer runs %.1f%% %s, corrected", size / 10000.0, ppm < 0 ? "fast" : "slow");
  return text;
}
std::string duration(uint64_t milliseconds) {
  uint64_t remaining = milliseconds / 1000;
  const uint64_t sizes[] = {86400, 3600, 60, 1};
  const char *names[] = {"day", "hour", "minute", "second"};
  std::string out;
  for (int i = 0; i < 4; ++i) {
    uint64_t amount = remaining / sizes[i];
    remaining %= sizes[i];
    if (amount) { if (!out.empty()) out += " "; out += count(amount, names[i]); }
  }
  return out.empty() ? "0 seconds" : out;
}
std::string seconds(uint64_t milliseconds) {
  if (milliseconds < 100) return "Less than 0.1 seconds";
  if (milliseconds >= 60000) return duration(milliseconds);
  char text[40];
  snprintf(text, sizeof(text), "%.1f seconds", milliseconds / 1000.0);
  return text;
}
std::string memory(uint64_t bytes) {
  if (bytes < 1000) return count(bytes, "byte");
  char text[40];
  snprintf(text, sizeof(text), "%.1f kB", bytes / 1000.0);
  return text;
}
std::string speed(uint64_t bytes, uint64_t milliseconds) {
  if (!bytes || !milliseconds) return "Not recorded";
  char text[40]; snprintf(text, sizeof(text), "%.1f kB/s", static_cast<double>(bytes) / milliseconds);
  return text;
}
std::string percent(uint64_t successes, uint64_t attempts) {
  if (!attempts) return "Not recorded";
  char text[32]; snprintf(text, sizeof(text), "%.0f%%", 100.0 * successes / attempts);
  return text;
}
std::string signal(int dbm, bool connected) {
  if (!connected) return "Not connected";
  const char *quality = dbm >= -60 ? "Strong" : dbm >= -70 ? "Good" : dbm >= -80 ? "Weak" : "Very weak";
  return std::string(quality) + " (" + std::to_string(dbm) + " dBm)";
}
static bool leap(int year) { return year % 4 == 0 && (year % 100 != 0 || year % 400 == 0); }
static int monthDays(int year, int month) {
  const int days[] = {31,28,31,30,31,30,31,31,30,31,30,31};
  return days[month - 1] + (month == 2 && leap(year));
}
static int64_t dayNumber(int year, int month, int day) {
  int64_t days = 0;
  for (int y = 1970; y < year; ++y) days += leap(y) ? 366 : 365;
  for (int m = 1; m < month; ++m) days += monthDays(year, m);
  return days + day - 1;
}
int64_t timestamp(const char *iso) {
  if (!iso || strlen(iso) < 20) return -1;
  int year, month, day, hour, minute, second, used = 0;
  if (sscanf(iso, "%4d-%2d-%2dT%2d:%2d:%2d%n", &year, &month, &day, &hour, &minute, &second, &used) != 6
      || used != 19 || year < 1970 || year > 2199 || month < 1 || month > 12 || day < 1
      || day > monthDays(year, month) || hour < 0 || hour > 23 || minute < 0 || minute > 59 || second < 0 || second > 59) return -1;
  const char *end = iso + used;
  if (*end == '.') { ++end; if (*end < '0' || *end > '9') return -1; while (*end >= '0' && *end <= '9') ++end; }
  int offset = 0;
  if (strcmp(end, "Z")) {
    if (strlen(end) != 6 || (end[0] != '+' && end[0] != '-') || end[3] != ':') return -1;
    for (int i : {1,2,4,5}) if (end[i] < '0' || end[i] > '9') return -1;
    int hours = (end[1] - '0') * 10 + end[2] - '0';
    int minutes = (end[4] - '0') * 10 + end[5] - '0';
    if (hours > 23 || minutes > 59) return -1;
    offset = (hours * 3600 + minutes * 60) * (end[0] == '+' ? 1 : -1);
  }
  return dayNumber(year, month, day) * 86400 + hour * 3600 + minute * 60 + second - offset;
}
bool expired(const char *iso, int64_t now) {
  int64_t value = timestamp(iso);
  return value >= 0 && now >= value;
}
int copenhagenOffset(int64_t utcSeconds) {
  time_t stamp = static_cast<time_t>(utcSeconds);
  struct tm utc;
  gmtime_r(&stamp, &utc);
  int year = utc.tm_year + 1900;
  auto transition = [&](int month) {
    int day = monthDays(year, month);
    day -= (dayNumber(year, month, day) + 4) % 7; // Last Sunday, 01:00 UTC.
    return dayNumber(year, month, day) * 86400 + 3600;
  };
  return utcSeconds >= transition(3) && utcSeconds < transition(10) ? 7200 : 3600;
}
std::string date(const char *iso) {
  int64_t value = timestamp(iso);
  if (value < 0) return "Not recorded";
  bool summer = copenhagenOffset(value) == 7200;
  time_t stamp = static_cast<time_t>(value + (summer ? 7200 : 3600));
  struct tm local;
  gmtime_r(&stamp, &local);
  char text[64];
  strftime(text, sizeof(text), "%d %b %Y, %H:%M:%S", &local);
  return std::string(text) + (summer ? " CEST" : " CET");
}
const char *outcome(const char *code) {
  if (!strcmp(code, "ok")) return "GCP login succeeded; a current board is available";
  if (!strcmp(code, "connected_no_publication")) return "GCP login succeeded; no current board is available";
  if (!strcmp(code, "not_checked")) return "GCP has not been checked yet";
  if (!strcmp(code, "not_configured")) return "Setup is incomplete; enter the Wi-Fi settings over USB";
  if (!strcmp(code, "wifi_disconnected")) return "Could not check GCP because Wi-Fi is disconnected";
  if (!strcmp(code, "clock_not_ready")) return "Waiting for the device clock before making a secure connection";
  if (!strcmp(code, "insufficient_memory")) return "Not enough free memory to complete the check";
  if (!strcmp(code, "credentials_rejected")) return "GCP rejected the device login or access permission";
  if (!strcmp(code, "rate_limited")) return "GCP asked the device to wait before trying again";
  if (!strcmp(code, "tls_or_network_failed")) return "Could not establish a verified secure connection to GCP";
  if (!strcmp(code, "connection_setup_failed")) return "Could not start the GCP connection";
  if (!strcmp(code, "publication_unavailable")) return "GCP service was unavailable; device login was not confirmed";
  if (!strcmp(code, "metadata_too_large")) return "GCP response exceeded the device's size limit";
  if (!strcmp(code, "incomplete_response")) return "The GCP response was interrupted or incomplete";
  if (!strcmp(code, "invalid_status_response")) return "GCP returned an unexpected or expired status response";
  return "GCP check failed; see the response code in technical details";
}
const char *wifiIssue(const char *code) {
  if (!strcmp(code, "none_recorded")) return "No interruption recorded since this device start";
  if (!strcmp(code, "network_not_found")) return "The configured network was not found";
  if (!strcmp(code, "authentication_failed")) return "The router rejected the Wi-Fi login";
  if (!strcmp(code, "security_handshake_timeout")) return "The Wi-Fi security check timed out";
  if (!strcmp(code, "association_failed")) return "The router did not accept the connection";
  if (!strcmp(code, "signal_lost")) return "The Wi-Fi signal was lost";
  if (!strcmp(code, "incompatible_security")) return "The router's security settings were not compatible";
  if (!strcmp(code, "signal_too_weak")) return "The Wi-Fi signal was too weak";
  return "The Wi-Fi connection was interrupted";
}
const char *restart(const char *code) {
  if (!strcmp(code, "power_on")) return "Power was switched on";
  if (!strcmp(code, "software")) return "Restarted by software or after saving settings";
  if (!strcmp(code, "brownout")) return "Restarted after a drop in supply voltage";
  if (!strcmp(code, "task_watchdog")) return "Restarted because a task stopped responding";
  if (!strcmp(code, "panic")) return "Restarted after a software error";
  return "Other restart reason";
}
std::string http(int code, bool attempted) {
  if (!code) return attempted ? "No HTTP response received" : "Not checked yet";
  const char *meaning = code == 200 ? "OK" : code == 401 ? "login rejected" : code == 403 ? "access denied"
    : code == 429 ? "too many requests" : code == 503 ? "service unavailable" : "unexpected response";
  return "HTTP " + std::to_string(code) + " (" + meaning + ")";
}
}
