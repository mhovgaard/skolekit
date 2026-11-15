// GCP sync by hand or on a wake schedule, local HTTPS diagnostics, panel drawing
// and deep sleep. Never fetches school sources; draws only verified saved frames.
// After each sync it posts its status document to GCP for the private dashboard.
#include "cloud_app.h"
#include "friendly_status.h"
#include "frame_cache.h"
#include "power.h"
#include <Arduino.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <cJSON.h>
#include <esp_crt_bundle.h>
#include <esp_heap_caps.h>
#include <esp_http_client.h>
#include <esp_https_server.h>
#include <esp_sntp.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <mbedtls/sha256.h>
#include <time.h>
#include <atomic>

namespace {
constexpr char kFirmware[] = "ag-cloud-0.7.1";
constexpr char kHostname[] = "firebeetle-ag";
constexpr size_t kMaxConfig = 8192;
constexpr size_t kMaxMetadata = 4096;
constexpr uint32_t kCheckIntervalMs = 10000;
struct Config {
  String ssid, password, host, gatewayKey, token, adminHash, cert, key;
  bool ready = false;
} config;
struct Result {
  uint32_t boots = 0, attempts = 0, successes = 0, failures = 0, consecutive = 0;
  uint32_t durationMs = 0, bytes = 0, lastMinHeap = 0;
  int http = 0;
  bool authenticated = false;
  char publication[16] = "unknown";
  char outcome[48] = "not_checked";
  char attemptedAt[32] = "", successAt[32] = "";
  char metadataAt[32] = "";
  char version[101] = "", generatedAt[40] = "", sourceAt[40] = "", expiresAt[40] = "";
  char freshness[32] = "";
} result;
Preferences preferences;
SemaphoreHandle_t stateMutex;
SemaphoreHandle_t reportMutex;
struct ReportGuard {
  ReportGuard() { xSemaphoreTake(reportMutex, portMAX_DELAY); }
  ~ReportGuard() { xSemaphoreGive(reportMutex); }
};
httpd_handle_t server = nullptr;
bool pendingCheck = false, runningCheck = false, hasChecked = false;
bool checkJsonOutput = false;
int pendingFrameMode = 0;
framecache::Metadata verifiedMetadata;
uint32_t lastCheckMs = 0;
char csrf[65];
uint32_t loginFailures = 0, loginBlockedUntil = 0;
bool mdnsReady = false;
std::atomic<uint16_t> lastDisconnectReason{0};
// A timer wake in sleep mode syncs once and sleeps; any other start stays awake.
bool scheduledWake = false, cycleQueued = false, cycleDone = false, clockNoted = false, timeSynced = false;
std::atomic<bool> sleepRequested{false};
std::atomic<uint32_t> awakeUntil{0};
uint32_t connectedAtMs = 0, lastBatteryMs = 0, lastSampleMs = 0;
int64_t nextAutoAt = 0, statusNextChange = 0, statusServerTime = 0;
// The last status report sent to GCP. Kept in RTC memory: it survives deep sleep
// without a flash write on every wake, and starts again as not sent on power-up.
struct StatusReport {
  char at[32];
  char outcome[32];
  int http;
  uint32_t bytes;
};
RTC_DATA_ATTR StatusReport lastReport = {"", "not_sent", 0, 0};

const char *wifiReason() {
  switch (lastDisconnectReason.load()) {
    case 0: return "none_recorded";
    case 15: case 204: return "security_handshake_timeout";
    case 201: return "network_not_found";
    case 202: return "authentication_failed";
    case 203: return "association_failed";
    case 200: return "signal_lost";
    case 210: case 211: return "incompatible_security";
    case 212: return "signal_too_weak";
    default: return "connection_failed";
  }
}

void lockState() { xSemaphoreTake(stateMutex, portMAX_DELAY); }
void unlockState() { xSemaphoreGive(stateMutex); }
bool clockReady() { return time(nullptr) >= 1767225600; } // 2026-01-01
void utcNow(char *out, size_t length) {
  out[0] = 0;
  if (!clockReady()) return;
  time_t now = time(nullptr);
  struct tm utc;
  gmtime_r(&now, &utc);
  strftime(out, length, "%Y-%m-%dT%H:%M:%SZ", &utc);
}
std::string isoAt(int64_t value) {
  if (value < 1767225600) return "";
  time_t stamp = static_cast<time_t>(value);
  struct tm utc;
  gmtime_r(&stamp, &utc);
  char out[32];
  strftime(out, sizeof(out), "%Y-%m-%dT%H:%M:%SZ", &utc);
  return out;
}
void stayAwake(uint32_t ms) {
  const uint32_t until = millis() + ms;
  if (static_cast<int32_t>(until - awakeUntil.load()) > 0) awakeUntil = until;
}
const char *modeName(uint8_t mode) { return mode == power::kSleep ? "sleep" : mode == power::kAwake ? "awake" : "manual"; }
const char *wakeName(power::Wake cause) { return cause == power::kTimer ? "timer" : cause == power::kButton ? "button" : "start"; }
const char *planText(const char *reason) {
  if (!strcmp(reason, "board_changes")) return "the board changes";
  if (!strcmp(reason, "scan_published")) return "a new board is published";
  if (!strcmp(reason, "retry")) return "retry after a failed wake";
  if (!strcmp(reason, "test_interval")) return "test interval";
  if (!strcmp(reason, "battery_low")) return "battery low · Wi-Fi and display skipped";
  if (!strcmp(reason, "clock_unknown")) return "retry · the clock is not set";
  return "longest sleep";
}
const char *reportText(const char *outcome) {
  if (!strcmp(outcome, "accepted")) return "Received by GCP";
  if (!strcmp(outcome, "not_sent")) return "Not sent yet · sent after each sync";
  if (!strcmp(outcome, "credentials_rejected")) return "Rejected · device credential";
  if (!strcmp(outcome, "not_supported")) return "GCP does not accept status reports yet";
  if (!strcmp(outcome, "rate_limited")) return "Rate limited · sent again after the next sync";
  if (!strcmp(outcome, "insufficient_memory")) return "Skipped · not enough free memory";
  if (!strcmp(outcome, "wifi_disconnected")) return "Skipped · Wi-Fi disconnected";
  if (!strcmp(outcome, "tls_or_network_failed")) return "Failed · could not connect";
  return "Failed · sent again after the next sync";
}
const char *cycleText(const char *outcome) {
  if (!strcmp(outcome, "board_unchanged")) return "board unchanged";
  if (!strcmp(outcome, "board_updated")) return "new board drawn";
  if (!strcmp(outcome, "no_board_published")) return "no board published";
  if (!strcmp(outcome, "download_failed")) return "board download failed";
  if (!strcmp(outcome, "draw_failed")) return "drawing failed";
  if (!strcmp(outcome, "check_failed")) return "GCP check failed";
  if (!strcmp(outcome, "wifi_unavailable")) return "Wi-Fi not available";
  if (!strcmp(outcome, "battery_low")) return "battery low · skipped";
  return "no sync recorded";
}
String digest(const char *value) {
  unsigned char hash[32];
  mbedtls_sha256(reinterpret_cast<const unsigned char *>(value), strlen(value), hash, 0);
  char hex[65];
  for (size_t i = 0; i < 32; ++i) snprintf(hex + i * 2, 3, "%02x", hash[i]);
  return String(hex);
}
bool constantEqual(const char *a, const char *b) {
  if (strlen(a) != strlen(b)) return false;
  volatile unsigned char mismatch = 0;
  for (size_t i = 0; a[i]; ++i) mismatch |= a[i] ^ b[i];
  return mismatch == 0;
}
const char *stringField(cJSON *obj, const char *key) {
  cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, key);
  return cJSON_IsString(item) ? item->valuestring : nullptr;
}
bool boundedField(cJSON *obj, const char *key, String &out, size_t min, size_t max) {
  const char *s = stringField(obj, key);
  if (!s || strlen(s) < min || strlen(s) > max) return false;
  out = s;
  return true;
}
bool headerSafe(const String &value) {
  for (size_t i = 0; i < value.length(); ++i)
    if (value[i] < 33 || value[i] > 126) return false;
  return true;
}
bool parseConfig(const char *json, Config &out) {
  cJSON *obj = cJSON_Parse(json);
  if (!obj) return false;
  bool valid = boundedField(obj, "ssid", out.ssid, 1, 32)
    && boundedField(obj, "wifi_password", out.password, 8, 63)
    && boundedField(obj, "gateway_host", out.host, 12, 150)
    && boundedField(obj, "gateway_key", out.gatewayKey, 20, 256)
    && boundedField(obj, "token", out.token, 32, 256)
    && boundedField(obj, "admin_auth_hash", out.adminHash, 64, 64)
    && boundedField(obj, "server_cert", out.cert, 200, 2200)
    && boundedField(obj, "server_key", out.key, 100, 2200);
  cJSON_Delete(obj);
  valid = valid && out.host.endsWith(".gateway.dev") && headerSafe(out.token)
    && headerSafe(out.gatewayKey)
    && out.cert.startsWith("-----BEGIN CERTIFICATE-----")
    && out.key.startsWith("-----BEGIN PRIVATE KEY-----");
  for (size_t i = 0; i < out.host.length(); ++i)
    if (!(isalnum(out.host[i]) || out.host[i] == '.' || out.host[i] == '-')) valid = false;
  for (size_t i = 0; i < out.adminHash.length(); ++i)
    if (!isxdigit(out.adminHash[i]) || isupper(out.adminHash[i])) valid = false;
  out.ready = valid;
  return valid;
}
const char *resetReason() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "power_on";
    case ESP_RST_SW: return "software";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_TASK_WDT: return "task_watchdog";
    case ESP_RST_PANIC: return "panic";
    default: return "other";
  }
}
cJSON *readableStatus(cJSON *raw);
// The live transfer report has a fixed small allocation budget. It deliberately
// omits history; simultaneous TLS must never require the full JSON document.
String progressJson(int frameMode, const framecache::State &cache) {
  const char *stage = !cache.busy ? "checking_gcp" : cache.phase;
  const char *description = !strcmp(stage, "checking_gcp") ? "Checking GCP for the current board" : framecache::describe(stage);
  unsigned long received = cache.busy ? cache.receivedBytes : 0;
  unsigned long readback = cache.busy ? cache.readbackBytes : 0;
  char json[1800];
  snprintf(json, sizeof(json),
    "{\"firmware\":\"%s\",\"device_id\":\"%s\",\"check_busy\":true,\"frame_busy\":%s,"
    "\"frame_stage\":\"%s\",\"frame_download_bytes\":%lu,\"frame_readback_bytes\":%lu,\"free_heap\":%lu,"
    "\"units\":{\"frame_download_bytes\":\"bytes\",\"frame_readback_bytes\":\"bytes\",\"free_heap\":\"bytes\"},"
    "\"readable\":[{\"section\":\"Status\",\"label\":\"Device\",\"value\":\"Running — responding now\"},"
    "{\"section\":\"Sync in progress\",\"label\":\"Stage\",\"value\":\"%s\"},"
    "{\"section\":\"Sync in progress\",\"label\":\"Bytes received so far\",\"value\":\"%lu bytes\"},"
    "{\"section\":\"Sync in progress\",\"label\":\"Bytes read back so far\",\"value\":\"%lu bytes\"},"
    "{\"section\":\"Sync in progress\",\"label\":\"Status report\",\"value\":\"Syncing · refresh for latest progress\"}]}",
    kFirmware, kHostname, (cache.busy || frameMode) ? "true" : "false", stage,
    received, readback, static_cast<unsigned long>(ESP.getFreeHeap()), description, received, readback);
  return String(json);
}
cJSON *statusDocument() {
  lockState();
  Result snapshot = result;
  bool busy = runningCheck || pendingCheck;
  int frameMode = pendingFrameMode;
  unlockState();
  framecache::State cache = framecache::snapshot();
  framecache::SyncHistory sync = framecache::syncSnapshot();
  if (busy) return cJSON_Parse(progressJson(frameMode, cache).c_str());
  cJSON *obj = cJSON_CreateObject();
  auto text = [&](const char *key, const char *value) { cJSON_AddItemToObjectCS(obj, key, cJSON_CreateString(value)); };
  auto number = [&](const char *key, double value) { cJSON_AddItemToObjectCS(obj, key, cJSON_CreateNumber(value)); };
  text("firmware", kFirmware);
  text("device_id", kHostname);
  cJSON_AddBoolToObject(obj, "configured", config.ready);
  cJSON_AddBoolToObject(obj, "wifi_connected", WiFi.status() == WL_CONNECTED);
  text("wifi_network", config.ready ? config.ssid.c_str() : "");
  number("wifi_status_code", WiFi.status());
  number("wifi_disconnect_code", lastDisconnectReason.load());
  text("wifi_disconnect_reason", wifiReason());
  cJSON_AddBoolToObject(obj, "clock_ready", clockReady());
  cJSON_AddBoolToObject(obj, "check_busy", busy);
  cJSON_AddBoolToObject(obj, "last_check_authenticated", snapshot.authenticated);
  text("publication", snapshot.publication);
  text("ip", WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString().c_str() : "");
  text("page", server ? "https://firebeetle-ag.local/" : "unavailable");
  text("counter_scope", "since_first_cloud_build_provisioning");
  text("reset_reason", resetReason());
  framecache::DisplayState shown = framecache::displaySnapshot();
  cJSON_AddBoolToObject(obj, "display_busy", shown.busy);
  cJSON_AddBoolToObject(obj, "display_current", *shown.version && !strcmp(shown.version, cache.cached.version) && cache.ready);
  text("display_result", shown.outcome);
  text("display_version", shown.version);
  text("display_generated_at", shown.generatedAt);
  text("display_drawn_at", shown.drawnAt);
  text("display_attempted_at", shown.attemptedAt);
  number("display_draws", shown.draws);
  number("display_failures", shown.failures);
  number("display_duration_ms", shown.durationMs);
  number("display_refresh_ms", shown.refreshMs);
  number("uptime_ms", esp_timer_get_time() / 1000);
  number("rssi_dbm", WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0);
  number("free_heap", ESP.getFreeHeap());
  number("minimum_free_heap", ESP.getMinFreeHeap());
  number("largest_free_block", heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
  number("boots", snapshot.boots);
  number("attempts", snapshot.attempts);
  number("successes", snapshot.successes);
  number("failures", snapshot.failures);
  number("consecutive_failures", snapshot.consecutive);
  number("last_http_status", snapshot.http);
  number("last_duration_ms", snapshot.durationMs);
  number("last_metadata_bytes", snapshot.bytes);
  number("last_check_minimum_heap", snapshot.lastMinHeap);
  text("last_result", snapshot.outcome);
  text("last_attempt_at", snapshot.attemptedAt);
  text("last_success_at", snapshot.successAt);
  text("last_metadata_at", snapshot.metadataAt);
  text("last_known_version", snapshot.version);
  text("last_known_generated_at", snapshot.generatedAt);
  text("last_known_source_updated_at", snapshot.sourceAt);
  text("last_known_expires_at", snapshot.expiresAt);
  text("last_known_freshness", snapshot.freshness);
  cJSON_AddBoolToObject(obj, "frame_ready", cache.ready && clockReady() && !friendly::expired(cache.cached.expiresAt, time(nullptr)));
  cJSON_AddBoolToObject(obj, "frame_busy", cache.busy || (busy && frameMode));
  cJSON_AddBoolToObject(obj, "frame_download_verified", cache.downloadVerified);
  cJSON_AddBoolToObject(obj, "frame_readback_verified", cache.readbackVerified);
  text("frame_stage", busy && frameMode == 1 && !cache.busy ? "checking_gcp" : cache.phase);
  text("frame_last_result", cache.outcome);
  text("frame_cached_version", cache.cached.version);
  text("frame_cached_sha256", cache.cached.sha256);
  text("frame_readback_sha256", cache.readbackHash);
  text("frame_expires_at", cache.cached.expiresAt);
  text("frame_verified_at", cache.verifiedAt);
  number("frame_attempts", cache.attempts);
  number("frame_successes", cache.successes);
  number("frame_failures", cache.failures);
  number("frame_download_bytes", cache.receivedBytes);
  number("frame_readback_bytes", cache.readbackBytes);
  number("frame_readback_chunks", cache.readbackChunks);
  number("frame_buffer_bytes", framecache::kBufferBytes);
  number("frame_duration_ms", cache.durationMs);
  number("frame_http_status", cache.http);
  number("frame_minimum_heap", cache.minimumHeap);
  number("cache_capacity_bytes", cache.capacityBytes);
  number("cache_free_bytes", cache.freeBytes);
  cJSON_AddBoolToObject(obj, "sync_successful", sync.successful);
  text("sync_result", sync.outcome);
  text("sync_started_at", sync.startedAt);
  text("sync_completed_at", sync.completedAt);
  text("sync_last_success_at", sync.lastSuccessAt);
  number("sync_attempts", sync.attempts); number("sync_successes", sync.successes); number("sync_failures", sync.failures);
  number("sync_bytes", sync.bytes); number("sync_chunks", sync.chunks);
  number("sync_download_ms", sync.downloadMs); number("sync_total_ms", sync.totalMs);
  number("sync_http_status", sync.http);
  number("sync_frames_saved", sync.successful ? 1 : 0);
  number("sync_download_kbytes_per_second", sync.downloadMs ? static_cast<double>(sync.bytes) / sync.downloadMs : 0);
  number("sync_success_rate_percent", sync.attempts ? 100.0 * sync.successes / sync.attempts : 0);
  const power::Settings powerSettings = power::settings();
  const power::Record cycle = power::record();
  const uint16_t millivolts = power::lastBattery();
  const wake::Plan plan = clockReady() ? power::nextPlan(time(nullptr)) : wake::Plan{0, ""};
  text("power_mode", modeName(powerSettings.mode));
  number("wake_test_minutes", powerSettings.testMinutes);
  text("wake_cause", wakeName(power::wakeCause()));
  cJSON_AddBoolToObject(obj, "battery_present", power::batteryPresent(millivolts));
  number("battery_mv", millivolts);
  number("battery_percent", power::batteryPresent(millivolts) ? battery::percent(millivolts) : 0);
  text("battery_trend", battery::trend(cycle.battery));
  number("battery_scale_permille", powerSettings.scalePermille);
  cJSON *samples = cJSON_AddArrayToObject(obj, "battery_samples");
  for (uint8_t i = 0; i < cycle.battery.count; ++i) {
    cJSON *sample = cJSON_CreateObject();
    cJSON_AddStringToObject(sample, "at", isoAt(cycle.battery.samples[i].at).c_str());
    cJSON_AddNumberToObject(sample, "mv", cycle.battery.samples[i].millivolts);
    cJSON_AddItemToArray(samples, sample);
  }
  text("next_wake_at", isoAt(plan.at).c_str());
  text("next_wake_reason", plan.reason);
  text("next_board_change_at", isoAt(cycle.nextChangeAt).c_str());
  const int32_t remaining = static_cast<int32_t>(awakeUntil.load() - millis());
  number("awake_remaining_ms", powerSettings.mode == power::kSleep && !scheduledWake && remaining > 0 ? remaining : 0);
  number("wakes", cycle.wakes); number("timer_wakes", cycle.timerWakes); number("button_wakes", cycle.buttonWakes);
  number("wake_cycles", cycle.cycles); number("consecutive_wake_failures", cycle.failures);
  text("last_cycle_result", cycle.outcome);
  text("last_woke_at", isoAt(cycle.wokeAt).c_str());
  number("last_awake_ms", cycle.awakeMs);
  number("average_awake_ms", cycle.sleeps ? cycle.totalAwakeMs / cycle.sleeps : 0);
  number("last_wake_late_seconds", cycle.lateSeconds);
  const power::Timer timer = power::timer();
  number("timer_drift_ppm", timer.driftPpm);
  number("timer_drift_last_ppm", timer.lastPpm);
  number("timer_drift_samples", timer.samples);
  number("timer_sleep_seconds", timer.requested);
  lockState(); StatusReport report = lastReport; unlockState();
  text("report_result", report.outcome);
  text("report_at", report.at);
  number("report_http_status", report.http);
  number("report_bytes", report.bytes);
  char observed[32]; utcNow(observed, sizeof(observed));
  text("observed_at", observed);
  cJSON_AddItemToObject(obj, "readable", readableStatus(obj));
  cJSON *units = cJSON_AddObjectToObject(obj, "units");
  for (const char *key : {"free_heap", "minimum_free_heap", "largest_free_block", "last_check_minimum_heap", "last_metadata_bytes"})
    cJSON_AddStringToObject(units, key, "bytes");
  for (const char *key : {"uptime_ms", "last_duration_ms", "display_duration_ms", "display_refresh_ms"}) cJSON_AddStringToObject(units, key, "milliseconds");
  for (const char *key : {"attempts", "successes", "failures", "consecutive_failures"}) cJSON_AddStringToObject(units, key, "checks");
  cJSON_AddStringToObject(units, "boots", "device starts");
  cJSON_AddStringToObject(units, "rssi_dbm", "dBm");
  cJSON_AddStringToObject(units, "last_http_status", "HTTP response code");
  cJSON_AddStringToObject(units, "wifi_status_code", "Wi-Fi connection status code");
  cJSON_AddStringToObject(units, "wifi_disconnect_code", "Wi-Fi disconnect reason code");
  for (const char *key : {"frame_download_bytes", "frame_readback_bytes", "frame_buffer_bytes", "frame_minimum_heap", "cache_capacity_bytes", "cache_free_bytes"})
    cJSON_AddStringToObject(units, key, "bytes");
  for (const char *key : {"frame_attempts", "frame_successes", "frame_failures"}) cJSON_AddStringToObject(units, key, "cache checks");
  cJSON_AddStringToObject(units, "frame_duration_ms", "milliseconds");
  for (const char *key : {"display_draws", "display_failures"}) cJSON_AddStringToObject(units, key, "panel draws");
  cJSON_AddStringToObject(units, "frame_readback_chunks", "chunks");
  cJSON_AddStringToObject(units, "frame_http_status", "HTTP response code");
  for (const char *key : {"sync_attempts", "sync_successes", "sync_failures"}) cJSON_AddStringToObject(units, key, "syncs");
  cJSON_AddStringToObject(units, "sync_bytes", "bytes");
  cJSON_AddStringToObject(units, "sync_chunks", "chunks");
  cJSON_AddStringToObject(units, "sync_download_ms", "milliseconds");
  cJSON_AddStringToObject(units, "sync_total_ms", "milliseconds");
  cJSON_AddStringToObject(units, "sync_http_status", "HTTP response code");
  cJSON_AddStringToObject(units, "sync_frames_saved", "frames");
  cJSON_AddStringToObject(units, "sync_download_kbytes_per_second", "kB/s");
  cJSON_AddStringToObject(units, "sync_success_rate_percent", "%");
  cJSON_AddStringToObject(units, "battery_mv", "millivolts");
  cJSON_AddStringToObject(units, "battery_percent", "% (estimate)");
  cJSON_AddStringToObject(units, "battery_scale_permille", "per mille of the raw reading");
  cJSON_AddStringToObject(units, "wake_test_minutes", "minutes");
  for (const char *key : {"awake_remaining_ms", "last_awake_ms", "average_awake_ms"}) cJSON_AddStringToObject(units, key, "milliseconds");
  cJSON_AddStringToObject(units, "last_wake_late_seconds", "seconds");
  for (const char *key : {"timer_drift_ppm", "timer_drift_last_ppm"}) cJSON_AddStringToObject(units, key, "parts per million of a timer sleep");
  cJSON_AddStringToObject(units, "timer_drift_samples", "timer wakes");
  cJSON_AddStringToObject(units, "timer_sleep_seconds", "seconds");
  cJSON_AddStringToObject(units, "report_http_status", "HTTP response code");
  cJSON_AddStringToObject(units, "report_bytes", "bytes");
  for (const char *key : {"wakes", "timer_wakes", "button_wakes", "wake_cycles", "consecutive_wake_failures"}) cJSON_AddStringToObject(units, key, "wakes");
  return obj;
}
cJSON *readableStatus(cJSON *raw) {
  cJSON *rows = cJSON_CreateArray();
  auto text = [&](const char *key) { const char *v = stringField(raw, key); return v ? v : ""; };
  auto number = [&](const char *key) { cJSON *v = cJSON_GetObjectItemCaseSensitive(raw, key); return v ? v->valuedouble : 0; };
  auto yes = [&](const char *key) { return cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(raw, key)); };
  auto row = [&](const char *section, const char *label, const std::string &value, const char *state = "neutral") {
    cJSON *item = cJSON_CreateObject();
    cJSON_AddItemToObjectCS(item, "section", cJSON_CreateStringReference(section));
    cJSON_AddItemToObjectCS(item, "label", cJSON_CreateStringReference(label));
    cJSON_AddItemToObjectCS(item, "value", cJSON_CreateString(value.c_str()));
    cJSON_AddItemToObjectCS(item, "state", cJSON_CreateStringReference(state));
    cJSON_AddItemToArray(rows, item);
  };
  const bool connected = yes("wifi_connected"), synced = number("sync_attempts") > 0;
  const bool success = yes("sync_successful"), ready = yes("frame_ready");
  row("Status", "Device", "Online · " + friendly::duration(number("uptime_ms")), "ok");
  row("Status", "Wi-Fi", connected ? "Connected · " + std::string(text("wifi_network")) : "Disconnected", connected ? "ok" : "error");
  row("Status", "GCP login", yes("last_check_authenticated")
    ? "Authenticated · " + friendly::date(text("last_success_at"))
    : number("attempts") ? friendly::outcome(text("last_result")) : "Not checked",
    !connected ? "warn" : yes("last_check_authenticated") ? "ok" : number("attempts") ? "error" : "warn");
  row("Status", "Last sync", !synced ? "Not recorded · select Sync now" : success
    ? "Complete · " + friendly::date(text("sync_completed_at")) : framecache::describe(text("sync_result")),
    !synced ? "warn" : success ? "ok" : "error");
  row("Status", "Saved frame", ready ? "Ready · " + friendly::memory(framecache::kFrameBytes)
    : friendly::expired(text("frame_expires_at"), time(nullptr)) ? "Expired · sync required" : "Unavailable · sync required", ready ? "ok" : "warn");
  const bool drawn = *text("display_version");
  const bool drawFailed = strcmp(text("display_result"), "ok") && strcmp(text("display_result"), "not_drawn");
  row("Status", "Display", yes("display_busy") ? "Drawing · about 30 seconds"
    : drawFailed ? framecache::describe(text("display_result"))
    : !drawn ? "Not drawn yet · select Sync now"
    : yes("display_current") ? "Showing current board · drawn " + friendly::date(text("display_drawn_at"))
    : "Showing an older board · drawn " + friendly::date(text("display_drawn_at")),
    yes("display_busy") ? "warn" : drawFailed ? "error" : yes("display_current") ? "ok" : "warn");
  const bool present = yes("battery_present");
  const double millivolts = number("battery_mv"), level = number("battery_percent");
  char volts[48];
  snprintf(volts, sizeof(volts), "%.2f V · about %d%%", millivolts / 1000.0, static_cast<int>(level));
  const char *trend = text("battery_trend");
  row("Status", "Battery", !present ? "Not detected · running on USB power"
    : std::string(volts) + (!strcmp(trend, "rising") ? " · rising, probably charging" : !strcmp(trend, "falling") ? " · falling" : ""),
    !present ? "neutral" : millivolts < battery::kLowMv ? "error" : level < 20 ? "warn" : "ok");
  const char *mode = text("power_mode");
  const bool sleeping = !strcmp(mode, "sleep");
  row("Power", "Mode", sleeping ? "Sleeps between updates" : !strcmp(mode, "awake") ? "Always on · syncs automatically" : "Manual · syncs when asked");
  if (strcmp(mode, "manual")) {
    std::string next = friendly::date(text("next_wake_at")) + " · " + planText(text("next_wake_reason"));
    if (!strcmp(text("next_wake_reason"), "test_interval"))
      next += " of " + friendly::count(static_cast<uint64_t>(number("wake_test_minutes")), "minute");
    row("Power", sleeping ? "Next wake" : "Next automatic sync", next);
  }
  if (number("awake_remaining_ms"))
    row("Power", "Stays awake", "About " + friendly::count(static_cast<uint64_t>(number("awake_remaining_ms")) / 60000 + 1, "minute")
      + ", then sleeps · press the board's button to wake it", "warn");
  row("Power", "Last wake", friendly::date(text("last_woke_at")) + " · " + std::string(text("wake_cause")) + " · " + cycleText(text("last_cycle_result")),
    number("consecutive_wake_failures") ? "warn" : "neutral");
  row("Power", "Time awake", number("last_awake_ms") ? "Last " + friendly::seconds(number("last_awake_ms")) + " · average "
    + friendly::seconds(number("average_awake_ms")) : "Not recorded");
  if (number("timer_wakes"))
    row("Power", "Wake timing", std::to_string(abs(static_cast<int>(number("last_wake_late_seconds")))) + " seconds "
      + (number("last_wake_late_seconds") < 0 ? "early" : "late") + " on the last timer wake · "
      + friendly::timerDrift(static_cast<int32_t>(number("timer_drift_ppm")), static_cast<uint32_t>(number("timer_drift_samples"))));
  row("Power", "Wakes", friendly::count(static_cast<uint64_t>(number("wakes")), "wake") + " · "
    + std::to_string(static_cast<unsigned>(number("timer_wakes"))) + " timer · "
    + std::to_string(static_cast<unsigned>(number("button_wakes"))) + " button · "
    + std::to_string(static_cast<unsigned>(number("consecutive_wake_failures"))) + " failed in a row");
  row("Power", "Charging", "Not measured by this board · its red LED is on while charging");
  row("Last sync", "Frames saved", !synced ? "Not recorded" : success ? "1 of 1 frame" : "0 of 1 frame", !synced ? "neutral" : success ? "ok" : "error");
  row("Last sync", "Data received", synced ? friendly::memory(number("sync_bytes")) : "Not recorded");
  row("Last sync", "Download chunks", synced ? friendly::count(number("sync_chunks"), "chunk") : "Not recorded");
  row("Last sync", "Download speed", friendly::speed(number("sync_bytes"), number("sync_download_ms")));
  row("Last sync", "Download time", number("sync_download_ms") ? friendly::seconds(number("sync_download_ms")) : "Not recorded");
  row("Last sync", "Total sync time", synced ? friendly::seconds(number("sync_total_ms")) : "Not recorded");
  row("Last sync", "Verification", !synced ? "Not recorded" : success ? "Checksum OK · flash read-back OK" : "Incomplete", !synced ? "neutral" : success ? "ok" : "error");
  row("Totals", "Syncs", friendly::count(number("sync_attempts"), "sync"));
  row("Totals", "Succeeded / failed", std::to_string(static_cast<unsigned>(number("sync_successes"))) + " succeeded · "
    + std::to_string(static_cast<unsigned>(number("sync_failures"))) + " failed", !synced ? "neutral" : number("sync_failures") ? "warn" : "ok");
  row("Totals", "Success rate", friendly::percent(number("sync_successes"), number("sync_attempts")));
  row("Technical details", "Last successful sync", friendly::date(text("sync_last_success_at")));
  row("Technical details", "Frame version", *text("frame_cached_version") ? text("frame_cached_version") : "Not recorded");
  row("Technical details", "School information updated", friendly::date(text("last_known_source_updated_at")));
  row("Technical details", "Frame expires", friendly::date(text("frame_expires_at")));
  row("Technical details", "Wi-Fi signal", friendly::signal(number("rssi_dbm"), connected));
  row("Technical details", "Free RAM", friendly::memory(number("free_heap")));
  row("Technical details", "Lowest free RAM since restart", friendly::memory(number("minimum_free_heap")));
  row("Technical details", "Largest free RAM block", friendly::memory(number("largest_free_block")));
  row("Technical details", "Frame buffer", friendly::memory(number("frame_buffer_bytes")));
  row("Technical details", "Free frame storage", friendly::memory(number("cache_free_bytes")));
  row("Technical details", "Last sync response", friendly::http(number("sync_http_status"), synced));
  const bool reported = !strcmp(text("report_result"), "accepted");
  row("Technical details", "Cloud status report", strcmp(text("report_result"), "not_sent")
    ? std::string(reportText(text("report_result"))) + " · " + friendly::date(text("report_at"))
      + (reported ? " · " + friendly::memory(number("report_bytes")) : "")
    : reportText("not_sent"), reported ? "ok" : !strcmp(text("report_result"), "not_sent") ? "neutral" : "warn");
  row("Technical details", "Last cache check", framecache::describe(text("frame_last_result")));
  row("Technical details", "Cache read-back", friendly::count(number("frame_readback_bytes"), "byte"));
  row("Technical details", "Displayed version", drawn ? text("display_version") : "Not recorded");
  row("Technical details", "Panel refresh time", number("display_refresh_ms") ? friendly::seconds(number("display_refresh_ms")) : "Not recorded");
  row("Technical details", "Panel draws", std::to_string(static_cast<unsigned>(number("display_draws"))) + " succeeded · "
    + std::to_string(static_cast<unsigned>(number("display_failures"))) + " failed");
  row("Technical details", "Clock", yes("clock_ready") ? "Ready" : "Waiting for time", yes("clock_ready") ? "ok" : "warn");
  row("Technical details", "Firmware", text("firmware"));
  row("Technical details", "Address", text("page"));
  row("Technical details", "Device starts", friendly::count(number("boots"), "start"));
  row("Technical details", "Restart reason", friendly::restart(text("reset_reason")));
  row("Technical details", "SHA-256", text("frame_cached_sha256"));
  return rows;
}
void printReadableStatus(bool details = false) {
  ReportGuard reportGuard;
  cJSON *obj = statusDocument();
  cJSON *rows = cJSON_GetObjectItemCaseSensitive(obj, "readable");
  String previous;
  unsigned step = 0;
  Serial.println("AG School device");
  for (cJSON *row = rows ? rows->child : nullptr; row; row = row->next) {
    const char *section = stringField(row, "section");
    if (!details && !strcmp(section, "Technical details")) continue;
    if (previous != section) { Serial.printf("\n%s\n", section); previous = section; }
    const char *tone = stringField(row, "state");
    if (tone && !strcmp(section, "Status")) Serial.printf("%02u ", ++step);
    Serial.printf("%s: ", stringField(row, "label"));
    if (tone) Serial.printf("[%s] ", !strcmp(tone, "ok") ? "OK" : !strcmp(tone, "warn") ? "WAIT" : !strcmp(tone, "error") ? "FAIL" : "—");
    Serial.println(stringField(row, "value"));
  }
  Serial.println("\nTimes: Copenhagen (CET/CEST). History is saved across restarts.");
  if (details) Serial.println("Memory: kB = 1,000 bytes.");
  else Serial.println("Use info for technical details.");
  cJSON_Delete(obj);
}
String statusJson() {
  ReportGuard reportGuard;
  lockState(); bool active = runningCheck || pendingCheck; int mode = pendingFrameMode; unlockState();
  if (active) return progressJson(mode, framecache::snapshot());
  cJSON *obj = statusDocument();
  if (!obj) return "{\"error\":\"low_memory\"}";
  bool busy = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(obj, "check_busy"));
  // Avoid cJSON's doubling reallocations temporarily holding two print buffers.
  char *json = cJSON_PrintBuffered(obj, busy ? 12288 : 24576, false);
  cJSON_Delete(obj);
  String value = json ? json : "{\"error\":\"low_memory\"}";
  cJSON_free(json);
  return value;
}
String escaped(const char *value) {
  String out;
  for (; *value; ++value) {
    switch (*value) {
      case '&': out += "&amp;"; break;
      case '<': out += "&lt;"; break;
      case '>': out += "&gt;"; break;
      case '"': out += "&quot;"; break;
      case '\'': out += "&#39;"; break;
      default: out += *value;
    }
  }
  return out;
}
void responseHeaders(httpd_req_t *req) {
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  httpd_resp_set_hdr(req, "X-Content-Type-Options", "nosniff");
  httpd_resp_set_hdr(req, "X-Frame-Options", "DENY");
  httpd_resp_set_hdr(req, "Referrer-Policy", "no-referrer");
  httpd_resp_set_hdr(req, "Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'");
  httpd_resp_set_hdr(req, "Connection", "close");
}
bool authenticate(httpd_req_t *req) {
  responseHeaders(req);
  if (loginBlockedUntil && static_cast<int32_t>(loginBlockedUntil - millis()) > 0) {
    httpd_resp_set_status(req, "429 Too Many Requests");
    httpd_resp_set_hdr(req, "Retry-After", "30");
    httpd_resp_sendstr(req, "Login temporarily limited.");
    return false;
  }
  char header[256];
  size_t length = httpd_req_get_hdr_value_len(req, "Authorization");
  bool valid = length > 0 && length < sizeof(header)
    && httpd_req_get_hdr_value_str(req, "Authorization", header, sizeof(header)) == ESP_OK
    && constantEqual(digest(header).c_str(), config.adminHash.c_str());
  memset(header, 0, sizeof(header));
  if (valid) { loginFailures = 0; stayAwake(120000); return true; }
  if (length && ++loginFailures >= 5) {
    loginBlockedUntil = millis() + 30000;
    loginFailures = 0;
  }
  httpd_resp_set_status(req, "401 Unauthorized");
  httpd_resp_set_hdr(req, "WWW-Authenticate", "Basic realm=\"AG School device\", charset=\"UTF-8\"");
  httpd_resp_sendstr(req, "Device login required.");
  return false;
}
esp_err_t pageHandler(httpd_req_t *req) {
  if (!authenticate(req)) return ESP_OK;
  ReportGuard reportGuard;
  String page;
  page.reserve(10000);
  page = "<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>AG School device</title>"
    "<style>*{box-sizing:border-box}body{font:15px system-ui;line-height:1.45;max-width:760px;margin:0 auto;padding:28px 20px;color:#25352d;background:#f6f8f6}h1{font-size:30px;margin:4px 0 20px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:#67756c;margin:24px 0 8px}p{margin:8px 0}small,.intro{color:#67756c}table{border-collapse:collapse;width:100%;background:white;border:1px solid #dfe6e0}td,th{text-align:left;padding:11px 12px;border-bottom:1px solid #e8ede8;overflow-wrap:anywhere;vertical-align:top}th{width:38%;font-weight:500}td{font-variant-numeric:tabular-nums}.checks{counter-reset:step}.checks th:before{counter-increment:step;content:counter(step,decimal-leading-zero);color:#9aa59e;font-size:12px;margin-right:12px}.signal{display:inline-block;font-size:10px;font-weight:750;letter-spacing:.03em;margin-right:8px;min-width:30px;color:#67756c}.state-ok .signal,.state-ok td{color:#1c7040}.state-warn .signal,.state-warn td{color:#895800}.state-error .signal,.state-error td{color:#aa3030}.state-neutral .signal{display:none}button,a{font:inherit}button,.button{display:inline-block;text-decoration:none;border:1px solid #cad5cd;border-radius:5px;padding:9px 14px;background:white;color:#25352d;margin:0 8px 6px 0}.primary{background:#216a40;color:white;border-color:#216a40}form{display:inline}details{margin-top:24px}summary{cursor:pointer;font-weight:600}details table{margin:12px 0}footer{font-size:12px;color:#67756c;margin-top:24px}@media(max-width:480px){body{padding:20px 12px}td,th{padding:10px 9px}th{width:40%}.checks th:before{display:block;margin:0 0 2px}.signal{display:block;margin-bottom:2px}h1{font-size:27px}}</style>"
    "<p class=intro>AG board · FireBeetle</p><h1>Device status</h1>"
    "<form method=post action=/sync><input type=hidden name=csrf value='";
  page += csrf;
  page += "'><button class=primary>Sync now</button></form><a class=button href='/'>Refresh status</a>";
  if (power::settings().mode == power::kSleep) {
    for (const char *action : {"/stay-awake", "/sleep-now"}) {
      page += "<form method=post action='" + String(action) + "'><input type=hidden name=csrf value='" + String(csrf)
        + "'><button>" + String(action[2] == 't' ? "Stay awake 10 minutes" : "Sleep now") + "</button></form>";
    }
  }
  cJSON *obj = statusDocument();
  cJSON *rows = cJSON_GetObjectItemCaseSensitive(obj, "readable");
  String previous;
  for (cJSON *row = rows ? rows->child : nullptr; row; row = row->next) {
    const char *section = stringField(row, "section");
    if (!section) continue;
    if (previous != section) {
      if (previous.length()) page += "</table>";
      if (!strcmp(section, "Technical details")) page += "<details><summary>Technical details</summary>";
      else page += "<h2>" + escaped(section) + "</h2>";
      page += !strcmp(section, "Status") ? "<table class=checks>" : "<table>";
      previous = section;
    }
    const char *state = stringField(row, "state");
    if (!state) state = "neutral";
    const char *badge = !strcmp(state, "ok") ? "OK" : !strcmp(state, "warn") ? "WAIT" : !strcmp(state, "error") ? "FAIL" : "—";
    page += "<tr class='state-" + escaped(state) + "'><th scope=row>" + escaped(stringField(row, "label"))
      + "</th><td><span class=signal>" + badge + "</span>" + escaped(stringField(row, "value")) + "</td></tr>";
  }
  cJSON_Delete(obj);
  page += "</table>";
  if (previous == "Technical details") {
    for (const char *action : {"/check", "/frame-readback", "/draw"}) {
      page += "<form method=post action='" + String(action) + "'><input type=hidden name=csrf value='" + String(csrf)
        + "'><button>" + String(!strcmp(action, "/check") ? "Check connection" : !strcmp(action, "/draw") ? "Redraw display" : "Verify saved frame") + "</button></form>";
    }
    page += "<p><a href='/api/status'>Raw status (JSON)</a></p></details>";
  }
  page += "<footer>Times: Copenhagen (CET/CEST). kB = 1,000 bytes. Chunks are pieces read by this device. "
    "Sync now downloads the published board and draws it if it changed; Refresh status reads this device. Sync history survives restarts. "
    "In sleep mode the page is reachable only while the board is awake: press its button to wake it, or keep it awake, for ten minutes. "
    "Close the browser session to clear its login.</footer></html>";
  httpd_resp_set_type(req, "text/html; charset=utf-8");
  return httpd_resp_send(req, page.c_str(), page.length());
}

esp_err_t statusHandler(httpd_req_t *req) {
  if (!authenticate(req)) return ESP_OK;
  String json = statusJson();
  httpd_resp_set_type(req, "application/json");
  return httpd_resp_send(req, json.c_str(), json.length());
}
bool queueCheck(bool outputJson = false, int frameMode = 0) {
  lockState();
  bool allowed = !pendingCheck && !runningCheck
    && (!hasChecked || millis() - lastCheckMs >= kCheckIntervalMs);
  if (allowed) { pendingCheck = true; checkJsonOutput = outputJson; pendingFrameMode = frameMode; }
  unlockState();
  return allowed;
}
esp_err_t checkHandler(httpd_req_t *req) {
  if (!authenticate(req)) return ESP_OK;
  if (req->content_len != 69) return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid action.");
  char body[70];
  size_t received = 0;
  while (received < 69) {
    int n = httpd_req_recv(req, body + received, 69 - received);
    if (n <= 0) return ESP_FAIL;
    received += n;
  }
  body[69] = 0;
  if (strncmp(body, "csrf=", 5) || !constantEqual(body + 5, csrf))
    return httpd_resp_send_err(req, HTTPD_403_FORBIDDEN, "Invalid action token.");
  if (!strcmp(req->uri, "/stay-awake") || !strcmp(req->uri, "/sleep-now")) {
    if (power::settings().mode != power::kSleep) return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Only in sleep mode.");
    if (!strcmp(req->uri, "/sleep-now")) {
      sleepRequested = true;
      return httpd_resp_sendstr(req, "Going to sleep. Press the board's button to wake it.");
    }
    stayAwake(power::kMaintenanceMs);
    httpd_resp_set_status(req, "303 See Other");
    httpd_resp_set_hdr(req, "Location", "/");
    return httpd_resp_sendstr(req, "Staying awake.");
  }
  int mode = (!strcmp(req->uri, "/frame-test") || !strcmp(req->uri, "/sync")) ? 1 : !strcmp(req->uri, "/frame-readback") ? 2
    : !strcmp(req->uri, "/draw") ? 3 : 0;
  if (!queueCheck(false, mode)) {
    httpd_resp_set_status(req, "409 Conflict");
    return httpd_resp_sendstr(req, "A check is running or was just completed. Wait ten seconds.");
  }
  httpd_resp_set_status(req, "303 See Other");
  httpd_resp_set_hdr(req, "Location", "/");
  return httpd_resp_sendstr(req, "Check requested. Refresh status to see the result.");
}
void startServer() {
  httpd_ssl_config_t cfg = HTTPD_SSL_CONFIG_DEFAULT();
  cfg.servercert = reinterpret_cast<const uint8_t *>(config.cert.c_str());
  cfg.servercert_len = config.cert.length() + 1;
  cfg.prvtkey_pem = reinterpret_cast<const uint8_t *>(config.key.c_str());
  cfg.prvtkey_len = config.key.length() + 1;
  cfg.httpd.max_open_sockets = 1;
  cfg.httpd.lru_purge_enable = true;
  cfg.httpd.recv_wait_timeout = 5;
  cfg.httpd.send_wait_timeout = 5;
  cfg.tls_handshake_timeout_ms = 5000;
  if (httpd_ssl_start(&server, &cfg) != ESP_OK) {
    server = nullptr;
    Serial.println("{\"error\":\"maintenance_start_failed\"}");
    return;
  }
  httpd_uri_t page = {};
  page.uri = "/"; page.method = HTTP_GET; page.handler = pageHandler;
  httpd_register_uri_handler(server, &page);
  page.uri = "/api/status"; page.handler = statusHandler;
  httpd_register_uri_handler(server, &page);
  page.uri = "/check"; page.method = HTTP_POST; page.handler = checkHandler;
  httpd_register_uri_handler(server, &page);
  page.uri = "/sync";
  httpd_register_uri_handler(server, &page);
  page.uri = "/frame-test";
  httpd_register_uri_handler(server, &page);
  page.uri = "/frame-readback";
  httpd_register_uri_handler(server, &page);
  page.uri = "/draw";
  httpd_register_uri_handler(server, &page);
  page.uri = "/stay-awake";
  httpd_register_uri_handler(server, &page);
  page.uri = "/sleep-now";
  httpd_register_uri_handler(server, &page);
}
void persistResult() {
  // Fixed-layout versioned blob contains technical data only, never credentials.
  preferences.putBytes("result-v2", &result, sizeof(result));
}
bool copyField(cJSON *obj, const char *key, char *target, size_t size) {
  const char *value = stringField(obj, key);
  if (!value || !*value || strlen(value) >= size) return false;
  strlcpy(target, value, size);
  return true;
}
bool timestampValid(const char *value, bool future) {
  struct tm date = {};
  const char *end = strptime(value, "%Y-%m-%dT%H:%M:%S", &date);
  if (!end) return false;
  if (*end == '.') { ++end; if (!isdigit(*end)) return false; while (isdigit(*end)) ++end; }
  int offset = 0;
  if (strcmp(end, "Z")) {
    if (strlen(end) != 6 || (end[0] != '+' && end[0] != '-') || end[3] != ':'
        || !isdigit(end[1]) || !isdigit(end[2]) || !isdigit(end[4]) || !isdigit(end[5])) return false;
    int hours = (end[1] - '0') * 10 + end[2] - '0';
    int minutes = (end[4] - '0') * 10 + end[5] - '0';
    if (hours > 23 || minutes > 59) return false;
    offset = (hours * 3600 + minutes * 60) * (end[0] == '+' ? 1 : -1);
  }
  time_t stamp = mktime(&date) - offset; // Device timezone is explicitly UTC.
  return stamp >= 1767225600 && (!future || stamp > time(nullptr));
}
bool parseMetadata(cJSON *obj, Result &next, framecache::Metadata &metadata) {
  const char *format = stringField(obj, "frame_format");
  const char *hash = stringField(obj, "frame_sha256");
  auto equals = [&](const char *key, int value) {
    cJSON *field = cJSON_GetObjectItemCaseSensitive(obj, key);
    return cJSON_IsNumber(field) && field->valuedouble == value;
  };
  bool valid = equals("width", 800) && equals("height", 480) && equals("frame_bytes", 192000)
    && format && !strcmp(format, "spectra6-4bpp-high-nibble-first")
    && hash && strlen(hash) == 64
    && copyField(obj, "version", next.version, sizeof(next.version))
    && copyField(obj, "generated_at", next.generatedAt, sizeof(next.generatedAt))
    && copyField(obj, "source_updated_at", next.sourceAt, sizeof(next.sourceAt))
    && copyField(obj, "expires_at", next.expiresAt, sizeof(next.expiresAt))
    && copyField(obj, "status", next.freshness, sizeof(next.freshness));
  if (valid) {
    for (const char *p = hash; *p; ++p) if (!isxdigit(*p)) valid = false;
    for (const char *p = next.version; *p; ++p) if (!(isalnum(*p) || *p == '-' || *p == '_')) valid = false;
    valid = valid && timestampValid(next.generatedAt, false) && timestampValid(next.sourceAt, false)
      && timestampValid(next.expiresAt, true);
  }
  if (valid) {
    strlcpy(metadata.version, next.version, sizeof(metadata.version));
    strlcpy(metadata.sha256, hash, sizeof(metadata.sha256));
    for (char *p = metadata.sha256; *p; ++p) *p = tolower(*p);
    strlcpy(metadata.expiresAt, next.expiresAt, sizeof(metadata.expiresAt));
    strlcpy(metadata.generatedAt, next.generatedAt, sizeof(metadata.generatedAt));
    strlcpy(metadata.sourceAt, next.sourceAt, sizeof(metadata.sourceAt));
  }
  return valid;
}
bool parseConnection(const char *body, Result &next, framecache::Metadata &metadata) {
  cJSON *obj = cJSON_Parse(body);
  if (!obj) return false;
  cJSON *available = cJSON_GetObjectItemCaseSensitive(obj, "publication_available");
  cJSON *version = cJSON_GetObjectItemCaseSensitive(obj, "api_version");
  const char *device = stringField(obj, "device_id");
  const char *serverTime = stringField(obj, "server_time");
  bool valid = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(obj, "ok"))
    && cJSON_IsNumber(version) && version->valuedouble == 1
    && device && !strcmp(device, kHostname) && serverTime && timestampValid(serverTime, false)
    && cJSON_IsBool(available);
  if (valid && cJSON_IsTrue(available)) {
    valid = parseMetadata(cJSON_GetObjectItemCaseSensitive(obj, "metadata"), next, metadata);
    if (valid) {
      strlcpy(next.publication, "available", sizeof(next.publication));
      utcNow(next.metadataAt, sizeof(next.metadataAt));
    }
  } else if (valid) {
    valid = cJSON_IsNull(cJSON_GetObjectItemCaseSensitive(obj, "metadata"));
    if (valid) strlcpy(next.publication, "unavailable", sizeof(next.publication));
  }
  if (valid) {
    next.authenticated = true;
    // When the server's next pre-drawn frame takes over; absent with no successor.
    const char *change = stringField(obj, "next_change_at");
    int64_t at = change ? friendly::timestamp(change) : 0;
    statusNextChange = at > 0 ? at : 0;
    statusServerTime = friendly::timestamp(serverTime);
  }
  cJSON_Delete(obj);
  return valid;
}
bool runCheck(bool frameWork = false) {
  lockState();
  pendingCheck = false; runningCheck = true;
  bool outputJson = checkJsonOutput;
  Result next = result;
  framecache::Metadata metadata;
  unlockState();
  const uint32_t started = millis();
  ++next.attempts;
  next.http = 0; next.bytes = 0; next.authenticated = false;
  strlcpy(next.publication, "unknown", sizeof(next.publication));
  utcNow(next.attemptedAt, sizeof(next.attemptedAt));
  const char *outcome = "not_configured";
  if (!config.ready) outcome = "not_configured";
  else if (WiFi.status() != WL_CONNECTED) outcome = "wifi_disconnected";
  else if (!clockReady()) outcome = "clock_not_ready";
  else if (ESP.getFreeHeap() < 70000 || heap_caps_get_largest_free_block(MALLOC_CAP_8BIT) < 32768)
    outcome = "insufficient_memory";
  else {
    String url = "https://" + config.host + "/v1/display/status";
    esp_http_client_config_t cfg = {};
    cfg.url = url.c_str();
    cfg.crt_bundle_attach = esp_crt_bundle_attach;
    cfg.timeout_ms = 12000;
    cfg.disable_auto_redirect = true;
    cfg.buffer_size = 1024;
    cfg.buffer_size_tx = 1024;
    cfg.keep_alive_enable = false;
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (!client) outcome = "connection_setup_failed";
    else {
      esp_http_client_set_header(client, "x-api-key", config.gatewayKey.c_str());
      String authorization = "Bearer " + config.token;
      esp_http_client_set_header(client, "Authorization", authorization.c_str());
      esp_http_client_set_header(client, "Accept", "application/json");
      if (esp_http_client_open(client, 0) != ESP_OK) outcome = "tls_or_network_failed";
      else {
        int64_t length = esp_http_client_fetch_headers(client);
        next.http = esp_http_client_get_status_code(client);
        if (next.http == 401 || next.http == 403) outcome = "credentials_rejected";
        else if (next.http == 429) outcome = "rate_limited";
        else if (next.http == 503) outcome = "publication_unavailable";
        else if (next.http != 200) outcome = "http_error";
        else if (length > static_cast<int64_t>(kMaxMetadata)) outcome = "metadata_too_large";
        else {
          char *body = static_cast<char *>(calloc(kMaxMetadata + 1, 1));
          outcome = "insufficient_memory";
          if (body) {
            outcome = "incomplete_response";
            while (next.bytes < kMaxMetadata && millis() - started < 30000) {
              int count = esp_http_client_read(client, body + next.bytes, kMaxMetadata - next.bytes);
              if (count < 0) break;
              next.bytes += count;
              if (esp_http_client_is_complete_data_received(client)) {
                Result candidate = next;
                if (parseConnection(body, candidate, metadata)) {
                  next = candidate;
                  outcome = !strcmp(next.publication, "available") ? "ok" : "connected_no_publication";
                } else outcome = "invalid_status_response";
                break;
              }
              if (count == 0) break;
              delay(1);
            }
            free(body);
          }
        }
      }
      esp_http_client_close(client);
      esp_http_client_cleanup(client);
    }
  }
  next.durationMs = millis() - started;
  next.lastMinHeap = ESP.getMinFreeHeap();
  strlcpy(next.outcome, outcome, sizeof(next.outcome));
  if (next.authenticated) {
    // The server's time stands in for network time not yet received.
    if (!timeSynced && statusServerTime > 0) {
      timeval tv = {static_cast<time_t>(statusServerTime), 0};
      settimeofday(&tv, nullptr);
      timeSynced = true;
      power::clockSynced();
    }
    ++next.successes; next.consecutive = 0;
    utcNow(next.successAt, sizeof(next.successAt));
    power::noteChange(statusNextChange);
  } else { ++next.failures; ++next.consecutive; }
  lockState();
  result = next;
  verifiedMetadata = metadata;
  persistResult();
  runningCheck = frameWork;
  lastCheckMs = millis(); hasChecked = true;
  unlockState();
  if (!frameWork) {
    if (outputJson) Serial.println(statusJson());
    else printReadableStatus();
  }
  return next.authenticated;
}
// Posts this device's status document, as its page shows it, to GCP. The private
// dashboard then shows the latest one while the board sleeps. Technical data
// only; the unit table is left out to keep the request small.
void reportStatus() {
  StatusReport next = {};
  utcNow(next.at, sizeof(next.at));
  const char *outcome = "not_configured";
  char *body = nullptr;
  if (!config.ready) outcome = "not_configured";
  else if (WiFi.status() != WL_CONNECTED) outcome = "wifi_disconnected";
  else if (!clockReady()) outcome = "clock_not_ready";
  else if (ESP.getFreeHeap() < 90000 || heap_caps_get_largest_free_block(MALLOC_CAP_8BIT) < 32768)
    outcome = "insufficient_memory";
  else {
    {
      ReportGuard reportGuard;
      cJSON *obj = statusDocument();
      if (obj) {
        cJSON_DeleteItemFromObjectCaseSensitive(obj, "units");
        body = cJSON_PrintBuffered(obj, 16384, false);
        cJSON_Delete(obj);
      }
    }
    outcome = "insufficient_memory";
    if (body) {
      next.bytes = strlen(body);
      String url = "https://" + config.host + "/v1/display/device-status";
      esp_http_client_config_t cfg = {};
      cfg.url = url.c_str();
      cfg.method = HTTP_METHOD_POST;
      cfg.crt_bundle_attach = esp_crt_bundle_attach;
      cfg.timeout_ms = 12000;
      cfg.disable_auto_redirect = true;
      cfg.buffer_size = 1024;
      cfg.buffer_size_tx = 1024;
      cfg.keep_alive_enable = false;
      esp_http_client_handle_t client = esp_http_client_init(&cfg);
      if (!client) outcome = "connection_setup_failed";
      else {
        esp_http_client_set_header(client, "x-api-key", config.gatewayKey.c_str());
        String authorization = "Bearer " + config.token;
        esp_http_client_set_header(client, "Authorization", authorization.c_str());
        esp_http_client_set_header(client, "Content-Type", "application/json");
        if (esp_http_client_open(client, next.bytes) != ESP_OK) outcome = "tls_or_network_failed";
        else {
          size_t written = 0;
          while (written < next.bytes) {
            int count = esp_http_client_write(client, body + written, next.bytes - written);
            if (count <= 0) break;
            written += count;
          }
          if (written != next.bytes) outcome = "send_failed";
          else {
            esp_http_client_fetch_headers(client);
            next.http = esp_http_client_get_status_code(client);
            outcome = next.http == 202 ? "accepted" : next.http == 401 || next.http == 403 ? "credentials_rejected"
              : next.http == 404 ? "not_supported" : next.http == 429 ? "rate_limited" : "http_error";
          }
        }
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
      }
      cJSON_free(body);
    }
  }
  strlcpy(next.outcome, outcome, sizeof(next.outcome));
  lockState(); lastReport = next; unlockState();
}
// One automatic sync: check GCP, download only a board this device lacks, draw it.
void runScheduledSync() {
  const uint32_t started = millis();
  lockState(); pendingCheck = false; runningCheck = true; unlockState();
  const char *outcome = "check_failed";
  bool ok = false;
  if (runCheck(true)) {
    lockState(); framecache::Metadata metadata = verifiedMetadata; unlockState();
    framecache::State cache = framecache::snapshot();
    ok = true;
    if (!*metadata.version) outcome = "no_board_published";
    else if (cache.ready && !strcmp(cache.cached.version, metadata.version)) outcome = "board_unchanged";
    else {
      framecache::startSync();
      framecache::download(config.host.c_str(), config.gatewayKey.c_str(), config.token.c_str(), metadata);
      framecache::finishSync(millis() - started);
      ok = framecache::syncSnapshot().successful;
      outcome = ok ? "board_updated" : "download_failed";
    }
    const uint32_t failuresBefore = framecache::displaySnapshot().failures;
    framecache::display(false);
    if (framecache::displaySnapshot().failures != failuresBefore) { ok = false; outcome = "draw_failed"; }
  }
  power::finishCycle(outcome, ok);
  if (!scheduledWake) nextAutoAt = power::nextPlan(time(nullptr)).at;
  lockState(); runningCheck = false; lastCheckMs = millis(); hasChecked = true; unlockState();
  reportStatus();
  Serial.printf("{\"cycle\":\"%s\",\"battery_mv\":%u,\"duration_ms\":%lu,\"report\":\"%s\"}\n", outcome,
                power::lastBattery(), static_cast<unsigned long>(millis() - started), lastReport.outcome);
  cycleDone = true;
}
void runFrameWork(int mode) {
  if (mode == 4) { runScheduledSync(); return; }
  const uint32_t syncStarted = millis();
  if (mode == 1) framecache::startSync();
  lockState(); pendingCheck = false; runningCheck = true; bool outputJson = checkJsonOutput; unlockState();
  if (mode == 2) framecache::readback();
  else if (mode == 3) framecache::display(true);
  else if (runCheck(true)) {
    lockState(); framecache::Metadata metadata = verifiedMetadata; unlockState();
    if (*metadata.version) framecache::download(config.host.c_str(), config.gatewayKey.c_str(), config.token.c_str(), metadata);
    else framecache::failed("no_publication");
  } else framecache::failed("metadata_check_failed");
  if (mode == 1) framecache::finishSync(millis() - syncStarted);
  if (mode == 1) framecache::display(false);
  lockState(); runningCheck = false; lastCheckMs = millis(); hasChecked = true; unlockState();
  if (mode == 1) reportStatus();
  if (outputJson) Serial.println(statusJson()); else printReadableStatus();
}
bool parseNumber(const char *text, unsigned long max, unsigned long &value) {
  char *end;
  value = strtoul(text, &end, 10);
  return *text && !*end && value <= max;
}
void command(const char *line) {
  stayAwake(120000);
  unsigned long value = 0;
  if (!strcmp(line, "status") || !strcmp(line, "wifi")) printReadableStatus();
  else if (!strcmp(line, "info")) printReadableStatus(true);
  else if (!strcmp(line, "status-json")) Serial.println(statusJson());
  else if (!strcmp(line, "help")) Serial.println("Commands: status, info, wifi, scan, sync, check, frame-readback, draw, battery, "
    "power manual|awake|sleep, wake-test MINUTES, battery-calibrate MILLIVOLTS, sleep-now, help, reboot; "
    "status-json for raw diagnostics. Provision privately with firmware/device.py.");
  else if (!strncmp(line, "power ", 6)) {
    const char *name = line + 6;
    int mode = !strcmp(name, "manual") ? power::kManual : !strcmp(name, "awake") ? power::kAwake : !strcmp(name, "sleep") ? power::kSleep : -1;
    if (mode < 0 || !power::setMode(mode)) Serial.println("{\"error\":\"invalid_power_mode\"}");
    else { nextAutoAt = 0; stayAwake(power::kMaintenanceMs); Serial.printf("{\"power_mode\":\"%s\"}\n", modeName(mode)); }
  }
  else if (!strncmp(line, "wake-test ", 10)) {
    if (!parseNumber(line + 10, 720, value) || !power::setTestMinutes(value)) Serial.println("{\"error\":\"invalid_minutes\"}");
    else { nextAutoAt = 0; Serial.printf("{\"wake_test_minutes\":%lu}\n", value); }
  }
  else if (!strncmp(line, "battery-calibrate ", 18)) {
    if (!parseNumber(line + 18, 5000, value) || !power::calibrate(value)) Serial.println("{\"error\":\"calibration_rejected\"}");
    else Serial.printf("{\"battery_scale_permille\":%u,\"battery_mv\":%u}\n", power::settings().scalePermille, power::lastBattery());
  }
  else if (!strcmp(line, "battery")) {
    const uint16_t millivolts = power::readBattery();
    Serial.printf("{\"battery_mv\":%u,\"battery_present\":%s,\"battery_percent\":%d}\n", millivolts,
                  power::batteryPresent(millivolts) ? "true" : "false", power::batteryPresent(millivolts) ? battery::percent(millivolts) : 0);
  }
  else if (!strcmp(line, "sleep-now")) {
    if (power::settings().mode != power::kSleep) Serial.println("{\"error\":\"not_in_sleep_mode\"}");
    else { Serial.println("{\"sleeping\":true}"); sleepRequested = true; }
  }
  else if (!strcmp(line, "check")) Serial.println(queueCheck() ? "Checking GCP. Please wait..." : "A check is running or has just finished. Wait 10 seconds.");
  else if (!strcmp(line, "check-json")) Serial.println(queueCheck(true) ? "{\"check\":\"queued\"}" : "{\"error\":\"busy_or_cooldown\"}");
  else if (!strcmp(line, "sync")) Serial.println(queueCheck(false, 1) ? "Syncing board. Please wait..." : "Sync busy. Wait 10 seconds.");
  else if (!strcmp(line, "sync-json")) Serial.println(queueCheck(true, 1) ? "{\"check\":\"queued\"}" : "{\"error\":\"busy_or_cooldown\"}");
  else if (!strcmp(line, "frame-test") || !strcmp(line, "frame-readback")) Serial.println(queueCheck(false, !strcmp(line, "frame-test") ? 1 : 2) ? "Checking the frame. Please wait..." : "Another check is running or has just finished. Wait 10 seconds.");
  else if (!strcmp(line, "frame-test-json") || !strcmp(line, "frame-readback-json")) Serial.println(queueCheck(true, !strcmp(line, "frame-test-json") ? 1 : 2) ? "{\"check\":\"queued\"}" : "{\"error\":\"busy_or_cooldown\"}");
  else if (!strcmp(line, "draw")) Serial.println(queueCheck(false, 3) ? "Drawing the saved board. Please wait about 30 seconds..." : "Another check is running or has just finished. Wait 10 seconds.");
  else if (!strcmp(line, "draw-json")) Serial.println(queueCheck(true, 3) ? "{\"check\":\"queued\"}" : "{\"error\":\"busy_or_cooldown\"}");
  else if (!strcmp(line, "hello")) Serial.println("{\"protocol\":\"ag-cloud-1\",\"readable_status\":true,\"frame_cache\":true,\"display\":true,\"power\":true,\"status_report\":true}");
  else if (!strcmp(line, "scan")) {
    if (!config.ready) { Serial.println("{\"error\":\"not_configured\"}"); return; }
    if (WiFi.status() == WL_CONNECTED) { Serial.println("{\"error\":\"already_connected\"}"); return; }
    WiFi.disconnect(false, false);
    delay(100);
    int count = WiFi.scanNetworks(false, true);
    cJSON *obj = cJSON_CreateObject();
    cJSON_AddStringToObject(obj, "configured_network", config.ssid.c_str());
    cJSON_AddNumberToObject(obj, "scan_count", count);
    cJSON *matches = cJSON_AddArrayToObject(obj, "related_networks");
    String expected = config.ssid; expected.toLowerCase();
    expected.replace(" ", ""); expected.replace("-", ""); expected.replace("_", "");
    bool exact = false;
    for (int i = 0; i < count; ++i) {
      String name = WiFi.SSID(i), normal = name;
      if (name == config.ssid) exact = true;
      normal.toLowerCase(); normal.replace(" ", ""); normal.replace("-", ""); normal.replace("_", "");
      if (normal != expected && !normal.endsWith(expected.substring(expected.length() > 4 ? expected.length() - 4 : 0))) continue;
      if (cJSON_GetArraySize(matches) >= 8) continue;
      cJSON *network = cJSON_CreateObject();
      cJSON_AddStringToObject(network, "ssid", name.c_str());
      cJSON_AddNumberToObject(network, "channel", WiFi.channel(i));
      cJSON_AddNumberToObject(network, "rssi_dbm", WiFi.RSSI(i));
      cJSON_AddNumberToObject(network, "auth_mode", WiFi.encryptionType(i));
      cJSON_AddItemToArray(matches, network);
    }
    cJSON_AddBoolToObject(obj, "configured_network_visible", exact);
    char *json = cJSON_PrintUnformatted(obj);
    if (json) { Serial.println(json); cJSON_free(json); }
    cJSON_Delete(obj);
    WiFi.scanDelete();
    WiFi.begin(config.ssid.c_str(), config.password.c_str());
  }
  else if (!strncmp(line, "ssid ", 5)) {
    size_t length = strlen(line + 5);
    if (!config.ready || length < 1 || length > 32) { Serial.println("{\"error\":\"invalid_ssid\"}"); return; }
    String saved = preferences.getString("config", "");
    cJSON *obj = cJSON_Parse(saved.c_str());
    if (!obj) { Serial.println("{\"error\":\"invalid_config\"}"); return; }
    cJSON_ReplaceItemInObjectCaseSensitive(obj, "ssid", cJSON_CreateString(line + 5));
    char *json = cJSON_PrintUnformatted(obj);
    bool written = json && preferences.putString("config", json) == strlen(json);
    cJSON_free(json); cJSON_Delete(obj);
    Serial.println(written ? "{\"ssid_updated\":true}" : "{\"error\":\"config_write_failed\"}");
    if (written) { Serial.flush(); delay(200); ESP.restart(); }
  }
  else if (!strcmp(line, "reboot")) { Serial.println("Rebooting."); Serial.flush(); ESP.restart(); }
  else if (!strncmp(line, "time ", 5)) {
    char *end;
    long long seconds = strtoll(line + 5, &end, 10);
    if (*end || seconds < 1767225600LL || seconds > 2208988800LL) Serial.println("{\"error\":\"invalid_time\"}");
    else { timeval tv = {static_cast<time_t>(seconds), 0}; settimeofday(&tv, nullptr); Serial.println("{\"clock_set\":true}"); }
  } else if (!strncmp(line, "provision ", 10)) {
    Config proposed;
    if (!parseConfig(line + 10, proposed)) { Serial.println("{\"provisioned\":false}"); return; }
    if (server) { httpd_ssl_stop(server); server = nullptr; }
    if (preferences.putString("config", line + 10) != strlen(line + 10)) {
      Serial.println("{\"error\":\"config_write_failed\"}"); return;
    }
    Serial.println("{\"provisioned\":true}");
    Serial.flush(); delay(200); ESP.restart();
  } else Serial.println("{\"error\":\"unknown_command\"}");
}
void readCommands() {
  static char line[kMaxConfig + 16];
  static size_t used = 0;
  static bool overflow = false;
  // Bound each pass so input cannot starve the rest of the firmware.
  size_t quota = 2048;
  while (Serial.available() && quota--) {
    char ch = static_cast<char>(Serial.read());
    if (ch == '\n' || ch == '\r') {
      line[used] = 0;
      if (overflow) Serial.println("{\"error\":\"command_too_large\"}");
      else if (used) command(line);
      memset(line, 0, used); used = 0; overflow = false;
    } else if (used < sizeof(line) - 1) line[used++] = ch;
    else overflow = true;
  }
}
} // namespace

void cloudSetup() {
  setenv("TZ", "UTC0", 1);
  tzset();
  Serial.setRxBufferSize(12288);
  Serial.begin(115200);
  Serial.setDebugOutput(false);
  stateMutex = xSemaphoreCreateMutex();
  reportMutex = xSemaphoreCreateMutex();
  pinMode(2, OUTPUT);
  preferences.begin("ag-cloud", false);
  if (preferences.getBytesLength("result-v2") == sizeof(result)) preferences.getBytes("result-v2", &result, sizeof(result));
  power::begin();
  if (power::wakeCause() == power::kStart) { ++result.boots; persistResult(); }
  scheduledWake = power::settings().mode == power::kSleep && power::wakeCause() == power::kTimer;
  if (scheduledWake && power::batteryPresent(power::lastBattery()) && power::lastBattery() < battery::kLowMv) {
    // Keep the last board on the panel and protect the cell: no Wi-Fi, no refresh.
    power::finishCycle("battery_low", true);
    power::sleep(time(nullptr), wake::kMaximumSleep, "battery_low");
  }
  awakeUntil = millis() + power::kMaintenanceMs;
  framecache::begin();
  uint8_t random[32]; esp_fill_random(random, sizeof(random));
  for (size_t i = 0; i < sizeof(random); ++i) snprintf(csrf + i * 2, 3, "%02x", random[i]);
  String saved = preferences.getString("config", "");
  parseConfig(saved.c_str(), config);
  if (!scheduledWake) delay(500);
  Serial.printf("{\"firmware\":\"%s\",\"ready\":true,\"wake\":\"%s\",\"power_mode\":\"%s\"}\n", kFirmware,
                wakeName(power::wakeCause()), modeName(power::settings().mode));
  if (config.ready) {
    WiFi.persistent(false);
    WiFi.mode(WIFI_STA);
    WiFi.onEvent([](WiFiEvent_t, WiFiEventInfo_t info) {
      if (info.wifi_sta_disconnected.reason != 8) lastDisconnectReason.store(info.wifi_sta_disconnected.reason);
    }, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
    WiFi.setAutoReconnect(true);
    WiFi.begin(config.ssid.c_str(), config.password.c_str());
    configTime(0, 0, "time.google.com", "pool.ntp.org");
  } else Serial.println("Provision using firmware/device.py. No access point or cloud request started.");
}
void cloudLoop() {
  readCommands();
  const bool connected = WiFi.status() == WL_CONNECTED;
  if (connected && !connectedAtMs) connectedAtMs = millis();
  // A scheduled wake serves no page: it syncs, draws and sleeps again.
  if (!scheduledWake && config.ready && connected && !server) startServer();
  if (server && !mdnsReady && connected) {
    mdnsReady = MDNS.begin(kHostname);
    if (mdnsReady) MDNS.addService("https", "tcp", 443);
  }
  // Network time, or the server's time in a status reply, corrects the clock that
  // drifted in deep sleep. Without either, carry on a few seconds after connecting
  // with the clock as it is.
  if (!timeSynced && sntp_get_sync_status() == SNTP_SYNC_STATUS_COMPLETED) { timeSynced = true; power::clockSynced(); }
  if (!clockNoted && clockReady() && (timeSynced || (connectedAtMs && millis() - connectedAtMs > 8000))) {
    power::noteClock(time(nullptr));
    clockNoted = true;
  }
  lockState(); bool check = pendingCheck, busy = runningCheck; int mode = pendingFrameMode; unlockState();
  if (check) { if (mode) runFrameWork(mode); else runCheck(); }
  framecache::tick();
  // In sleep mode a press keeps the page up ten minutes, from any wake and even mid-sync.
  if (power::buttonPressed() && power::settings().mode == power::kSleep) {
    if (scheduledWake) {
      scheduledWake = false;
      // A finished timer sync is not repeated; one not yet queued runs as on a button wake.
      if (cycleDone) nextAutoAt = power::nextPlan(time(nullptr)).at;
    }
    sleepRequested = false;
    stayAwake(power::kMaintenanceMs);
    Serial.println("{\"button\":\"stay_awake\"}");
  }
  const uint8_t powerMode = power::settings().mode;
  if (scheduledWake) {
    // The clock kept running in deep sleep and is close enough for TLS, so sync as
    // soon as Wi-Fi is up; the status reply then corrects it before the next plan.
    if (!cycleQueued && connected && clockReady()) cycleQueued = queueCheck(false, 4);
    else if (!cycleQueued && millis() > 30000) { power::finishCycle("wifi_unavailable", false); cycleDone = true; }
    if (cycleDone) {
      if (!clockNoted && clockReady()) { power::noteClock(time(nullptr)); clockNoted = true; }
      power::sleep(time(nullptr));
    }
  } else {
    if (powerMode != power::kManual && config.ready && clockNoted && !check && !busy
        && (!nextAutoAt || time(nullptr) >= nextAutoAt)) queueCheck(false, 4);
    if (powerMode == power::kSleep && !check && !busy && !framecache::snapshot().busy
        && (sleepRequested || static_cast<int32_t>(millis() - awakeUntil.load()) >= 0)) {
      if (server) { httpd_ssl_stop(server); server = nullptr; }
      power::sleep(time(nullptr));
    }
    if (millis() - lastBatteryMs >= 60000) {
      lastBatteryMs = millis();
      if (millis() - lastSampleMs >= 30 * 60000) { lastSampleMs = millis(); power::sampleBattery(); }
      else power::readBattery();
    }
  }
  digitalWrite(2, scheduledWake ? LOW : connected ? (millis() % 2000 < 100) : (millis() % 500 < 250));
  delay(2);
}
