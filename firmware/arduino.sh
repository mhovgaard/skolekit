#!/bin/sh
# Keep Arduino packages, caches, and sketches inside the ignored local toolchain.
set -eu
firmware_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export ARDUINO_DIRECTORIES_DATA="$firmware_dir/.toolchain/data"
export ARDUINO_DIRECTORIES_DOWNLOADS="$firmware_dir/.toolchain/downloads"
export ARDUINO_DIRECTORIES_USER="$firmware_dir/.toolchain/user"
exec "$firmware_dir/.toolchain/bin/arduino-cli" \
  --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json \
  "$@"
