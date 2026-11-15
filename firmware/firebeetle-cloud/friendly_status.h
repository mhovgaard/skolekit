#pragma once
#include <cstdint>
#include <string>

namespace friendly {
std::string duration(uint64_t milliseconds);
std::string seconds(uint64_t milliseconds);
std::string memory(uint64_t bytes);
std::string speed(uint64_t bytes, uint64_t milliseconds);
std::string percent(uint64_t successes, uint64_t attempts);
std::string count(uint64_t value, const char *noun);
// The deep-sleep timer's learned drift, e.g. "timer runs 1.9% fast, corrected".
std::string timerDrift(int32_t ppm, uint32_t samples);
std::string signal(int dbm, bool connected);
std::string date(const char *iso);
const char *outcome(const char *code);
const char *wifiIssue(const char *code);
const char *restart(const char *code);
std::string http(int code, bool attempted);
bool expired(const char *iso, int64_t now);
int64_t timestamp(const char *iso);
// Seconds Copenhagen is ahead of UTC at that instant: 3600 (CET) or 7200 (CEST).
int copenhagenOffset(int64_t utcSeconds);
}
