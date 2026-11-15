#include "sync_history.h"
#include <cassert>
#include <cstring>

int main() {
  framecache::SyncHistory sync;
  assert(sync.attempts == 0 && !sync.successful);
  sync.start("2026-10-24T11:30:00Z");
  sync.bytes = 192000; sync.chunks = 12; sync.downloadMs = 6000;
  sync.complete("2026-10-24T11:30:08Z", "ok", 8000, true, true, 192000, 12);
  assert(sync.successful && sync.attempts == 1 && sync.successes == 1 && sync.failures == 0);
  assert(sync.bytes == 192000 && sync.chunks == 12 && sync.totalMs == 8000);
  auto persisted = sync;
  persisted.interrupted(); // A clean restart must preserve the last sync.
  assert(persisted.successful && persisted.bytes == 192000 && persisted.downloadMs == 6000);
  sync.start("2026-10-24T12:00:00Z");
  sync.bytes = 16000; sync.chunks = 1;
  sync.complete("2026-10-24T12:00:02Z", "incomplete_frame", 2000, false, false, 0, 0);
  assert(!sync.successful && sync.attempts == 2 && sync.successes == 1 && sync.failures == 1);
  assert(!strcmp(sync.lastSuccessAt, "2026-10-24T11:30:08Z"));
  sync.start("2026-10-24T13:00:00Z"); sync.bytes = 192000;
  sync.complete("2026-10-24T13:00:07Z", "ok", 7000, true, false, 191999, 12);
  assert(!sync.successful && sync.failures == 2); // Never green without full readback.
  sync.start("2026-10-24T14:00:00Z"); sync.interrupted();
  assert(!sync.busy && !sync.successful && sync.failures == 3 && !strcmp(sync.outcome, "interrupted"));
  sync.interrupted(); assert(sync.failures == 3);
}
