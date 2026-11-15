#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace framecache {
constexpr size_t kFrameBytes = 192000;
constexpr size_t kBufferBytes = 16000;

// Shared by the HTTPS download and flash read-back. SHA-256 is calculated by
// mbedTLS; this class enforces size, palette and the final digest comparison.
class Validator {
public:
  bool push(const uint8_t *bytes, size_t length) {
    if (error_) return false;
    if (length > kFrameBytes - count_) { error_ = "too_many_bytes"; return false; }
    for (size_t i = 0; i < length; ++i) {
      if (!colour(bytes[i] >> 4) || !colour(bytes[i] & 15)) {
        error_ = "invalid_colour"; return false;
      }
    }
    count_ += length;
    return true;
  }
  bool finish(const char *actual, const char *expected) {
    if (error_) return false;
    if (count_ != kFrameBytes) { error_ = "incomplete_frame"; return false; }
    if (!hash(actual) || !hash(expected) || strcmp(actual, expected)) {
      error_ = "checksum_mismatch"; return false;
    }
    return true;
  }
  size_t count() const { return count_; }
  const char *error() const { return error_ ? error_ : "ok"; }
  static bool hash(const char *value) {
    if (!value || strlen(value) != 64) return false;
    for (size_t i = 0; i < 64; ++i)
      if (!((value[i] >= '0' && value[i] <= '9') || (value[i] >= 'a' && value[i] <= 'f'))) return false;
    return true;
  }
private:
  static bool colour(uint8_t code) { return code <= 3 || code == 5 || code == 6; }
  size_t count_ = 0;
  const char *error_ = nullptr;
};
}
