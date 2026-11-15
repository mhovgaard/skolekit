#pragma once
#include <cstdint>
#include "frame_validator.h"
#include "sync_history.h"

namespace framecache {
struct Metadata {
  char version[101] = "";
  char sha256[65] = "";
  char expiresAt[40] = "";
  char generatedAt[40] = "";
  char sourceAt[40] = "";
};
struct State {
  bool mounted = false, ready = false, busy = false;
  bool downloadVerified = false, readbackVerified = false;
  uint32_t attempts = 0, successes = 0, failures = 0;
  uint32_t receivedBytes = 0, readbackBytes = 0, readbackChunks = 0;
  uint32_t durationMs = 0, minimumHeap = 0, capacityBytes = 0, freeBytes = 0;
  int http = 0;
  char phase[32] = "not_tested";
  char outcome[48] = "not_tested";
  char attemptedAt[32] = "", verifiedAt[32] = "";
  char readbackHash[65] = "";
  Metadata cached;
};
// What the physical panel last showed; saved across restarts.
struct DisplayState {
  bool busy = false;
  uint32_t draws = 0, failures = 0, durationMs = 0, refreshMs = 0;
  char version[101] = "", generatedAt[40] = "";
  char drawnAt[32] = "", attemptedAt[32] = "";
  char outcome[48] = "not_drawn";
};
void begin();
void tick();
State snapshot();
SyncHistory syncSnapshot();
DisplayState displaySnapshot();
// Draws the saved frame unless the panel already shows that version. The file
// is checked again while streaming; the panel refreshes only if it matches.
void display(bool force);
bool displayNeeded();
void startSync();
void finishSync(uint32_t durationMs);
void download(const char *host, const char *gatewayKey, const char *token, const Metadata &metadata);
void readback();
void failed(const char *reason);
const char *describe(const char *code);
}
