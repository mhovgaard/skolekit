#include "frame_validator.h"
#include <cassert>
#include <string>
#include <vector>

int main() {
  using namespace framecache;
  std::vector<uint8_t> chunk(kBufferBytes, 0x11);
  std::string expected(64, 'a'), different(64, 'b');
  Validator valid;
  for (int i = 0; i < 12; ++i) assert(valid.push(chunk.data(), chunk.size()));
  assert(valid.count() == 192000);
  assert(valid.finish(expected.c_str(), expected.c_str()));
  Validator shortFrame;
  for (int i = 0; i < 11; ++i) assert(shortFrame.push(chunk.data(), chunk.size()));
  assert(shortFrame.push(chunk.data(), chunk.size() - 1));
  assert(!shortFrame.finish(expected.c_str(), expected.c_str()));
  assert(std::string(shortFrame.error()) == "incomplete_frame");
  assert(!valid.push(chunk.data(), 1));
  assert(std::string(valid.error()) == "too_many_bytes");
  Validator corrupted;
  for (int i = 0; i < 12; ++i) assert(corrupted.push(chunk.data(), chunk.size()));
  assert(!corrupted.finish(different.c_str(), expected.c_str()));
  assert(std::string(corrupted.error()) == "checksum_mismatch");
  for (uint8_t code : {0x41, 0x14, 0x71, 0x1f, 0xf1}) {
    Validator palette;
    assert(!palette.push(&code, 1));
    assert(std::string(palette.error()) == "invalid_colour");
  }
  for (uint8_t code : {0x00, 0x11, 0x22, 0x33, 0x55, 0x66, 0x16}) {
    Validator palette;
    assert(palette.push(&code, 1));
  }
  assert(!Validator::hash(""));
  assert(!Validator::hash(std::string(64, 'g').c_str()));
  assert(Validator::hash(expected.c_str()));
}
