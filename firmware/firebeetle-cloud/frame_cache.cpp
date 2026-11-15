#include "frame_cache.h"
#include "friendly_status.h"
#include "panel.h"
#include <Arduino.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <esp_crt_bundle.h>
#include <esp_heap_caps.h>
#include <esp_http_client.h>
#include <mbedtls/sha256.h>
#include <cstddef>
#include <ctime>

namespace framecache {
namespace {
constexpr char kCurrent[] = "/frame.cache";
constexpr char kPending[] = "/frame.pending";
struct Header {
  char magic[8];
  uint32_t schema, payloadBytes;
  Metadata metadata;
  char cachedAt[32];
  uint8_t headerHash[32];
};
State state;
SyncHistory syncHistory;
DisplayState shown;
Preferences store;
SemaphoreHandle_t mutex;
bool bootChecked = false;
uint32_t lastExpiryCheck = 0;
void lock() { xSemaphoreTake(mutex, portMAX_DELAY); }
void unlock() { xSemaphoreGive(mutex); }
bool clockReady() { return time(nullptr) >= 1767225600; }
void utc(char *out, size_t length) {
  out[0] = 0;
  if (!clockReady()) return;
  time_t now = time(nullptr); struct tm date;
  gmtime_r(&now, &date); strftime(out, length, "%Y-%m-%dT%H:%M:%SZ", &date);
}
void phase(const char *name) { lock(); strlcpy(state.phase, name, sizeof(state.phase)); unlock(); }
void space() {
  uint32_t total = LittleFS.totalBytes(), used = LittleFS.usedBytes();
  lock(); state.capacityBytes = total; state.freeBytes = total >= used ? total - used : 0; unlock();
}
void persist() {
  State saved = snapshot();
  store.putBytes("history-v1", &saved, sizeof(saved));
}
class Sha {
public:
  Sha() { mbedtls_sha256_init(&context); good = mbedtls_sha256_starts(&context, 0) == 0; }
  ~Sha() { mbedtls_sha256_free(&context); }
  bool push(const uint8_t *data, size_t bytes) { good = good && mbedtls_sha256_update(&context, data, bytes) == 0; return good; }
  bool finish(char *out) {
    uint8_t hash[32];
    if (!good || mbedtls_sha256_finish(&context, hash)) return false;
    for (size_t i = 0; i < 32; ++i) snprintf(out + i * 2, 3, "%02x", hash[i]);
    return true;
  }
private:
  mbedtls_sha256_context context;
  bool good;
};
bool terminated(const char *value, size_t bytes) { return memchr(value, 0, bytes) != nullptr; }
bool metadataValid(const Metadata &m) {
  if (!terminated(m.version, sizeof(m.version)) || !terminated(m.sha256, sizeof(m.sha256))
      || !terminated(m.expiresAt, sizeof(m.expiresAt)) || !terminated(m.generatedAt, sizeof(m.generatedAt))
      || !terminated(m.sourceAt, sizeof(m.sourceAt)) || !*m.version || !Validator::hash(m.sha256)) return false;
  for (const char *p = m.version; *p; ++p)
    if (!(isalnum(static_cast<unsigned char>(*p)) || *p == '-' || *p == '_')) return false;
  return friendly::timestamp(m.expiresAt) >= 1767225600 && friendly::timestamp(m.generatedAt) >= 1767225600
    && friendly::timestamp(m.sourceAt) >= 1767225600;
}
bool headerValid(const Header &h) {
  uint8_t actual[32];
  return !memcmp(h.magic, "AGFRM01", 8) && h.schema == 1 && h.payloadBytes == kFrameBytes
    && metadataValid(h.metadata) && terminated(h.cachedAt, sizeof(h.cachedAt))
    && mbedtls_sha256(reinterpret_cast<const uint8_t *>(&h), offsetof(Header, headerHash), actual, 0) == 0
    && !memcmp(actual, h.headerHash, sizeof(actual));
}
const char *verifyFile(const char *path, Header &header, char *hash, uint32_t &bytes, uint32_t &chunks, uint8_t *buffer,
                       void (*sink)(const uint8_t *, size_t) = nullptr) {
  bytes = chunks = 0; hash[0] = 0;
  File file = LittleFS.open(path, "r");
  if (!file) return "cache_missing";
  if (file.size() != sizeof(Header) + kFrameBytes
      || file.read(reinterpret_cast<uint8_t *>(&header), sizeof(Header)) != sizeof(Header) || !headerValid(header)) return "cache_invalid";
  if (!clockReady()) return "clock_not_ready";
  if (friendly::expired(header.metadata.expiresAt, time(nullptr))) return "frame_expired";
  Validator validator; Sha digest;
  while (file.available()) {
    size_t length = file.read(buffer, kBufferBytes);
    if (!length) return "cache_read_failed";
    if (!validator.push(buffer, length)) return validator.error();
    if (!digest.push(buffer, length)) return "hash_failed";
    if (sink) sink(buffer, length);
    bytes += length; ++chunks;
    lock(); state.readbackBytes = bytes; state.readbackChunks = chunks; unlock();
    delay(1);
  }
  if (!digest.finish(hash)) return "hash_failed";
  if (!validator.finish(hash, header.metadata.sha256)) return validator.error();
  return friendly::expired(header.metadata.expiresAt, time(nullptr)) ? "frame_expired" : "ok";
}
void start(bool isDownload) {
  lock();
  ++state.attempts; state.busy = true; state.receivedBytes = 0; state.readbackBytes = 0; state.readbackChunks = 0;
  state.http = 0; state.durationMs = 0; state.readbackVerified = false;
  if (isDownload) state.downloadVerified = false;
  utc(state.attemptedAt, sizeof(state.attemptedAt));
  strlcpy(state.phase, isDownload ? "downloading" : "reading_cache", sizeof(state.phase));
  unlock();
}
void finish(const char *outcome, uint32_t started) {
  lock();
  state.busy = false; state.durationMs = millis() - started; state.minimumHeap = ESP.getMinFreeHeap();
  strlcpy(state.outcome, outcome, sizeof(state.outcome));
  strlcpy(state.phase, !strcmp(outcome, "ok") ? "ready" : "failed", sizeof(state.phase));
  if (!strcmp(outcome, "ok")) ++state.successes; else ++state.failures;
  unlock();
  if (state.mounted) space();
  persist();
}
void accept(const Header &header, const char *hash) {
  lock();
  state.ready = true; state.readbackVerified = true; state.cached = header.metadata;
  strlcpy(state.verifiedAt, header.cachedAt, sizeof(state.verifiedAt));
  strlcpy(state.readbackHash, hash, sizeof(state.readbackHash));
  unlock();
}
struct ResponseHeaders { char version[101] = "", format[64] = "", type[96] = ""; bool overflow = false; };
esp_err_t responseEvent(esp_http_client_event_t *event) {
  if (event->event_id != HTTP_EVENT_ON_HEADER) return ESP_OK;
  auto &headers = *static_cast<ResponseHeaders *>(event->user_data);
  char *target = nullptr; size_t capacity = 0;
  if (!strcasecmp(event->header_key, "X-Display-Version")) { target = headers.version; capacity = sizeof(headers.version); }
  else if (!strcasecmp(event->header_key, "X-Frame-Format")) { target = headers.format; capacity = sizeof(headers.format); }
  else if (!strcasecmp(event->header_key, "Content-Type")) { target = headers.type; capacity = sizeof(headers.type); }
  if (target) {
    if (strlen(event->header_value) >= capacity) headers.overflow = true;
    strlcpy(target, event->header_value, capacity);
  }
  return ESP_OK;
}
} // namespace

State snapshot() { lock(); State copy = state; unlock(); return copy; }
SyncHistory syncSnapshot() { lock(); SyncHistory copy = syncHistory; unlock(); return copy; }
void saveDisplay() { DisplayState copy = displaySnapshot(); copy.busy = false; store.putBytes("display-v1", &copy, sizeof(copy)); }
DisplayState displaySnapshot() { lock(); DisplayState copy = shown; unlock(); return copy; }
void saveSync() { SyncHistory copy = syncSnapshot(); store.putBytes("sync-v1", &copy, sizeof(copy)); }
void startSync() {
  char now[32]; utc(now, sizeof(now));
  lock(); syncHistory.start(now); unlock(); saveSync();
}
void finishSync(uint32_t durationMs) {
  char now[32]; utc(now, sizeof(now));
  lock();
  syncHistory.http = state.http;
  syncHistory.complete(now, state.outcome, durationMs, state.downloadVerified,
                       state.readbackVerified, state.readbackBytes, state.readbackChunks);
  unlock(); saveSync();
}
void begin() {
  mutex = xSemaphoreCreateMutex();
  store.begin("ag-frame", false);
  if (store.getBytesLength("sync-v1") == sizeof(syncHistory)) store.getBytes("sync-v1", &syncHistory, sizeof(syncHistory));
  if (syncHistory.busy) { syncHistory.interrupted(); saveSync(); }
  if (store.getBytesLength("display-v1") == sizeof(shown)) store.getBytes("display-v1", &shown, sizeof(shown));
  shown.busy = false;
  panel::begin();
  if (store.getBytesLength("history-v1") == sizeof(state)) store.getBytes("history-v1", &state, sizeof(state));
  state.ready = false; state.busy = false; state.readbackVerified = false;
  state.mounted = LittleFS.begin(false, "/littlefs", 4, "spiffs");
  // This partition is dedicated to replaceable image cache; Wi-Fi and keys are
  // in the separate NVS partition. Initialize only if no filesystem can mount.
  if (!state.mounted) state.mounted = LittleFS.begin(true, "/littlefs", 4, "spiffs");
  if (!state.mounted) { strlcpy(state.phase, "storage_unavailable", sizeof(state.phase)); return; }
  if (LittleFS.exists(kPending)) LittleFS.remove(kPending); // Interrupted, never published.
  strlcpy(state.phase, LittleFS.exists(kCurrent) ? "waiting_for_clock" : "not_tested", sizeof(state.phase));
  space();
}
void tick() {
  if (!state.mounted || state.busy || !clockReady()) return;
  if (!bootChecked) {
    bootChecked = true;
    if (LittleFS.exists(kCurrent)) readback();
    display(false);
  }
  if (millis() - lastExpiryCheck < 1000) return;
  lastExpiryCheck = millis();
  State copy = snapshot();
  if (copy.ready && friendly::expired(copy.cached.expiresAt, time(nullptr))) {
    lock(); state.ready = false; strlcpy(state.phase, "expired", sizeof(state.phase)); unlock();
    LittleFS.remove(kCurrent); space();
  }
}
void failed(const char *reason) { uint32_t now = millis(); start(true); finish(reason, now); }
void download(const char *host, const char *gatewayKey, const char *token, const Metadata &metadata) {
  const uint32_t started = millis(); start(true);
  const char *outcome = "download_failed";
  uint8_t *buffer = nullptr;
  File output;
  esp_http_client_handle_t client = nullptr;
  Header header = {};
  do {
    if (!state.mounted) { outcome = "storage_unavailable"; break; }
    if (!clockReady()) { outcome = "clock_not_ready"; break; }
    if (!metadataValid(metadata)) { outcome = "invalid_metadata"; break; }
    if (friendly::expired(metadata.expiresAt, time(nullptr))) { outcome = "frame_expired"; break; }
    space();
    if (snapshot().freeBytes < kFrameBytes + sizeof(Header) + 32768) { outcome = "storage_full"; break; }
    // Browser TLS can split the heap without exhausting it. Require room for
    // two 16 kB allocations, rather than an unused contiguous 50 kB block.
    if (ESP.getFreeHeap() < 100000 || heap_caps_get_largest_free_block(MALLOC_CAP_8BIT) < 32768) { outcome = "insufficient_memory"; break; }
    buffer = static_cast<uint8_t *>(malloc(kBufferBytes));
    if (!buffer) { outcome = "insufficient_memory"; break; }
    output = LittleFS.open(kPending, "w");
    if (!output) { outcome = "cache_write_failed"; break; }
    memcpy(header.magic, "AGFRM01", 8); header.schema = 1; header.payloadBytes = kFrameBytes;
    header.metadata = metadata; utc(header.cachedAt, sizeof(header.cachedAt));
    if (mbedtls_sha256(reinterpret_cast<const uint8_t *>(&header), offsetof(Header, headerHash), header.headerHash, 0)
        || output.write(reinterpret_cast<const uint8_t *>(&header), sizeof(header)) != sizeof(header)) { outcome = "cache_write_failed"; break; }
    String url = String("https://") + host + "/v1/display/frame?version=" + metadata.version;
    ResponseHeaders response;
    esp_http_client_config_t cfg = {};
    cfg.url = url.c_str(); cfg.crt_bundle_attach = esp_crt_bundle_attach;
    cfg.timeout_ms = 12000; cfg.disable_auto_redirect = true;
    cfg.buffer_size = 1024; cfg.buffer_size_tx = 1024;
    cfg.event_handler = responseEvent; cfg.user_data = &response;
    client = esp_http_client_init(&cfg);
    if (!client) { outcome = "connection_setup_failed"; break; }
    esp_http_client_set_header(client, "x-api-key", gatewayKey);
    String authorization = String("Bearer ") + token;
    esp_http_client_set_header(client, "Authorization", authorization.c_str());
    esp_http_client_set_header(client, "Accept", "application/octet-stream");
    if (esp_http_client_open(client, 0) != ESP_OK) { outcome = "tls_or_network_failed"; break; }
    int64_t expectedLength = esp_http_client_fetch_headers(client);
    int http = esp_http_client_get_status_code(client);
    lock(); state.http = http; unlock();
    if (http != 200) {
      outcome = http == 409 ? "version_changed" : (http == 401 || http == 403) ? "credentials_rejected" : http == 429 ? "rate_limited" : "frame_http_error";
      break;
    }
    if ((expectedLength > 0 && expectedLength != kFrameBytes) || response.overflow
        || strcmp(response.version, metadata.version) || strcmp(response.format, "spectra6-4bpp-high-nibble-first")
        || strncmp(response.type, "application/octet-stream", 24)) { outcome = "invalid_frame_response"; break; }
    Validator validator; Sha digest;
    outcome = "incomplete_frame";
    while (millis() - started < 60000) {
      int count = esp_http_client_read(client, reinterpret_cast<char *>(buffer), kBufferBytes);
      if (count < 0) { outcome = "download_interrupted"; break; }
      if (count > 0) {
        lock(); state.receivedBytes += count; syncHistory.bytes += count; ++syncHistory.chunks; unlock();
        if (!validator.push(buffer, count)) { outcome = validator.error(); break; }
        if (!digest.push(buffer, count)) { outcome = "hash_failed"; break; }
        if (output.write(buffer, count) != static_cast<size_t>(count)) { outcome = "cache_write_failed"; break; }
      }
      if (esp_http_client_is_complete_data_received(client)) {
        char actual[65];
        if (!digest.finish(actual)) outcome = "hash_failed";
        else if (!validator.finish(actual, metadata.sha256)) outcome = validator.error();
        else outcome = "ok";
        break;
      }
      if (!count) break;
      delay(1);
    }
    lock(); syncHistory.downloadMs = millis() - started; unlock();
    if (strcmp(outcome, "ok")) break;
    lock(); state.downloadVerified = true; unlock();
    utc(header.cachedAt, sizeof(header.cachedAt));
    if (mbedtls_sha256(reinterpret_cast<const uint8_t *>(&header), offsetof(Header, headerHash), header.headerHash, 0)
        || !output.seek(0) || output.write(reinterpret_cast<const uint8_t *>(&header), sizeof(header)) != sizeof(header)) {
      outcome = "cache_write_failed"; break;
    }
    output.flush(); output.close();
    // Release cloud TLS buffers before the independent flash read-back.
    esp_http_client_close(client); esp_http_client_cleanup(client); client = nullptr;
    phase("reading_cache");
    char readHash[65]; uint32_t bytes = 0, chunks = 0;
    outcome = verifyFile(kPending, header, readHash, bytes, chunks, buffer);
    if (strcmp(outcome, "ok")) break;
    if (friendly::expired(header.metadata.expiresAt, time(nullptr))) { outcome = "frame_expired"; break; }
    // LittleFS rename atomically replaces the previous cache. Only verified
    // pending data reaches this point, so a failed transfer preserves the old file.
    if (!LittleFS.rename(kPending, kCurrent)) { outcome = "cache_publish_failed"; break; }
    accept(header, readHash); bootChecked = true;
  } while (false);
  if (output) output.close();
  if (client) { esp_http_client_close(client); esp_http_client_cleanup(client); }
  free(buffer);
  if (state.mounted && LittleFS.exists(kPending)) LittleFS.remove(kPending);
  finish(outcome, started);
}
void readback() {
  const uint32_t started = millis(); start(false);
  const char *outcome = "storage_unavailable";
  if (state.mounted) {
    uint8_t *buffer = static_cast<uint8_t *>(malloc(kBufferBytes));
    outcome = "insufficient_memory";
    if (buffer) {
      Header header; char hash[65]; uint32_t bytes = 0, chunks = 0;
      outcome = verifyFile(kCurrent, header, hash, bytes, chunks, buffer);
      if (!strcmp(outcome, "ok")) accept(header, hash);
      else {
        lock(); state.ready = false; unlock();
        if (strcmp(outcome, "clock_not_ready")) LittleFS.remove(kCurrent);
      }
      free(buffer);
    }
  }
  finish(outcome, started);
}
bool displayNeeded() {
  State cache = snapshot(); DisplayState panelState = displaySnapshot();
  return cache.ready && !cache.busy && strcmp(cache.cached.version, panelState.version);
}
void display(bool force) {
  State cache = snapshot();
  if (!force && !displayNeeded()) return;
  const uint32_t started = millis();
  char previousPhase[sizeof(state.phase)];
  lock();
  strlcpy(previousPhase, state.phase, sizeof(previousPhase));
  state.busy = true; strlcpy(state.phase, "drawing", sizeof(state.phase));
  shown.busy = true; utc(shown.attemptedAt, sizeof(shown.attemptedAt));
  unlock();
  const char *outcome = "no_saved_frame";
  Header header; uint32_t refreshMs = 0;
  if (state.mounted && cache.ready) {
    uint8_t *buffer = static_cast<uint8_t *>(malloc(kBufferBytes));
    outcome = "insufficient_memory";
    if (buffer) {
      outcome = "panel_not_responding";
      if (panel::open()) {
        char hash[65]; uint32_t bytes = 0, chunks = 0;
        // A rejected file leaves the old picture: the controller only shows
        // its image RAM after the refresh command.
        outcome = verifyFile(kCurrent, header, hash, bytes, chunks, buffer, panel::write);
        if (!strcmp(outcome, "ok") && !panel::refresh(refreshMs)) outcome = "panel_refresh_timeout";
        panel::sleep();
      }
      free(buffer);
    }
  }
  lock();
  state.busy = false; strlcpy(state.phase, previousPhase, sizeof(state.phase));
  shown.busy = false; shown.durationMs = millis() - started;
  strlcpy(shown.outcome, outcome, sizeof(shown.outcome));
  if (!strcmp(outcome, "ok")) {
    ++shown.draws; shown.refreshMs = refreshMs;
    strlcpy(shown.version, header.metadata.version, sizeof(shown.version));
    strlcpy(shown.generatedAt, header.metadata.generatedAt, sizeof(shown.generatedAt));
    strlcpy(shown.drawnAt, shown.attemptedAt, sizeof(shown.drawnAt));
  } else ++shown.failures;
  unlock();
  saveDisplay();
}
const char *describe(const char *code) {
  if (!strcmp(code, "ok") || !strcmp(code, "ready")) return "Verified, cached and read back — ready for display transfer";
  if (!strcmp(code, "interrupted")) return "Interrupted · retry sync";
  if (!strcmp(code, "not_recorded")) return "Not recorded";
  if (!strcmp(code, "not_tested")) return "Not recorded";
  if (!strcmp(code, "downloading")) return "Downloading and checking the frame";
  if (!strcmp(code, "drawing")) return "Drawing the board on the display (about 30 seconds)";
  if (!strcmp(code, "no_saved_frame")) return "No verified saved frame to draw; sync first";
  if (!strcmp(code, "panel_not_responding")) return "The display did not respond; check its wiring and power";
  if (!strcmp(code, "panel_refresh_timeout")) return "The display did not finish refreshing; check its wiring";
  if (!strcmp(code, "reading_cache")) return "Reading the saved frame back from flash";
  if (!strcmp(code, "waiting_for_clock") || !strcmp(code, "clock_not_ready")) return "Waiting for the correct time before trusting the saved frame";
  if (!strcmp(code, "frame_expired") || !strcmp(code, "expired")) return "The cached board expired; download a current board";
  if (!strcmp(code, "no_publication")) return "No current board is available from GCP";
  if (!strcmp(code, "metadata_check_failed")) return "Could not obtain verified current board information from GCP";
  if (!strcmp(code, "storage_unavailable")) return "The image storage could not be opened";
  if (!strcmp(code, "storage_full")) return "Not enough flash storage for a safe replacement";
  if (!strcmp(code, "insufficient_memory")) return "Not enough free RAM; close other device pages and retry";
  if (!strcmp(code, "checksum_mismatch")) return "Checksum mismatch — the new frame was rejected";
  if (!strcmp(code, "invalid_colour")) return "Invalid screen colour data — the new frame was rejected";
  if (!strcmp(code, "incomplete_frame") || !strcmp(code, "download_interrupted")) return "Incomplete download · retry sync";
  if (!strcmp(code, "too_many_bytes") || !strcmp(code, "invalid_frame_response")) return "Unexpected frame size or format — the new frame was rejected";
  if (!strcmp(code, "version_changed")) return "GCP published another board during the download — retry";
  if (!strcmp(code, "credentials_rejected")) return "GCP rejected the frame-download permission";
  if (!strcmp(code, "rate_limited")) return "GCP asked the device to wait before retrying";
  if (!strcmp(code, "cache_missing")) return "No saved frame is present";
  if (!strcmp(code, "cache_invalid")) return "The saved frame is damaged or has an unexpected format";
  if (!strcmp(code, "cache_write_failed") || !strcmp(code, "cache_publish_failed")) return "Could not finish saving the verified frame";
  if (!strcmp(code, "cache_read_failed")) return "Could not read the saved frame";
  if (!strcmp(code, "tls_or_network_failed")) return "Could not establish a verified secure download connection";
  return "Sync failed · inspect technical details";
}
}
