#pragma once
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace framecache {
// Kept separately from the cache-check state: local readbacks never replace it.
struct SyncHistory {
  uint32_t attempts = 0, successes = 0, failures = 0;
  uint32_t bytes = 0, chunks = 0, downloadMs = 0, totalMs = 0;
  uint32_t readbackBytes = 0, readbackChunks = 0;
  int http = 0;
  bool busy = false, successful = false, downloadVerified = false, readbackVerified = false;
  char startedAt[32] = "", completedAt[32] = "", lastSuccessAt[32] = "";
  char outcome[48] = "not_recorded";
  void start(const char *now) {
    ++attempts; busy = true; successful = downloadVerified = readbackVerified = false;
    bytes = chunks = downloadMs = totalMs = readbackBytes = readbackChunks = 0; http = 0;
    snprintf(startedAt, sizeof(startedAt), "%s", now); completedAt[0] = 0;
    snprintf(outcome, sizeof(outcome), "%s", "running");
  }
  void complete(const char *now, const char *result, uint32_t duration,
                bool downloaded, bool readback, uint32_t savedBytes, uint32_t savedChunks) {
    busy = false; totalMs = duration; downloadVerified = downloaded; readbackVerified = readback;
    readbackBytes = savedBytes; readbackChunks = savedChunks;
    successful = !strcmp(result, "ok") && downloaded && readback && bytes == 192000 && savedBytes == 192000;
    snprintf(outcome, sizeof(outcome), "%s", successful ? "ok" : !strcmp(result, "ok") ? "incomplete_frame" : result);
    snprintf(completedAt, sizeof(completedAt), "%s", now);
    if (successful) { ++successes; snprintf(lastSuccessAt, sizeof(lastSuccessAt), "%s", now); }
    else ++failures;
  }
  void interrupted() {
    if (!busy) return;
    busy = false; successful = false; ++failures;
    snprintf(outcome, sizeof(outcome), "%s", "interrupted");
  }
};
}
