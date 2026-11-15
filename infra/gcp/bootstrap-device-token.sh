#!/usr/bin/env bash

set -euo pipefail

PROJECT_ID="${1:-}"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "Usage: $0 PROJECT_ID" >&2
  exit 2
fi

if [[ "$(gcloud config get-value project 2>/dev/null)" != "${PROJECT_ID}" ]]; then
  echo "Refusing to change a project that is not active in gcloud." >&2
  exit 1
fi

if gcloud secrets versions list agskoleintra-device-token-hash \
  --project="${PROJECT_ID}" \
  --filter='state=ENABLED' \
  --format='value(name)' | grep -q .; then
  echo "The device-token hash already has an enabled version; no change made."
  exit 0
fi

TOKEN_HASH="$(uv run python -m skoleklar.device_token create)"
printf %s "${TOKEN_HASH}" | gcloud secrets versions add \
  agskoleintra-device-token-hash \
  --project="${PROJECT_ID}" \
  --data-file=- >/dev/null
unset TOKEN_HASH

echo "Generated the device token in macOS Keychain and uploaded only its hash."
