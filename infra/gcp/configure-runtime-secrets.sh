#!/usr/bin/env bash

set -euo pipefail

PROJECT_ID="${1:-}"
ROTATE_OPENROUTER="false"
ROTATE_MESSAGE_HANDLE_KEY="false"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "Usage: $0 PROJECT_ID [--rotate-openrouter] [--rotate-message-handle-key]" >&2
  exit 2
fi

shift
for option in "$@"; do
  case "${option}" in
    --rotate-openrouter) ROTATE_OPENROUTER="true" ;;
    --rotate-message-handle-key) ROTATE_MESSAGE_HANDLE_KEY="true" ;;
    *)
      echo "Unknown option: ${option}" >&2
      exit 2
      ;;
  esac
done

if [[ "$(gcloud config get-value project 2>/dev/null)" != "${PROJECT_ID}" ]]; then
  echo "Refusing to configure a project that is not active in gcloud." >&2
  exit 1
fi

add_secret() {
  local secret="$1"
  local prompt="$2"
  local value
  read -r -s -p "${prompt}: " value </dev/tty
  printf '\n' >/dev/tty
  if [[ -z "${value}" ]]; then
    echo "${secret} was empty; no secret version created." >&2
    exit 1
  fi
  if [[ "${secret}" == "agskoleintra-openrouter-api-key" ]]; then
    if ! printf %s "${value}" | python3 -c '
import sys
import urllib.error
import urllib.request

key = sys.stdin.read()
request = urllib.request.Request(
    "https://openrouter.ai/api/v1/auth/key",
    headers={"Authorization": f"Bearer {key}"},
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"unexpected HTTP status {response.status}")
except urllib.error.HTTPError as error:
    print(f"OpenRouter rejected the key (HTTP {error.code}); nothing was uploaded.", file=sys.stderr)
    raise SystemExit(1)
except Exception as error:
    print(f"Could not validate the OpenRouter key: {type(error).__name__}; nothing was uploaded.", file=sys.stderr)
    raise SystemExit(1)
'; then
      unset value
      exit 1
    fi
    echo "OpenRouter accepted the key."
  fi
  printf %s "${value}" | gcloud secrets versions add "${secret}" \
    --project="${PROJECT_ID}" \
    --data-file=- >/dev/null
  unset value
  echo "Added a new enabled version to ${secret}."
}

add_secret_if_missing() {
  local secret="$1"
  local prompt="$2"
  if gcloud secrets versions list "${secret}" \
    --project="${PROJECT_ID}" \
    --filter='state=ENABLED' \
    --format='value(name)' 2>/dev/null | grep -q .; then
    echo "${secret} already has an enabled version; leaving it unchanged."
    return
  fi
  add_secret "${secret}" "${prompt}"
}

add_generated_message_handle_key() {
  local generated_key
  generated_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48), end="")')"
  printf %s "${generated_key}" | gcloud secrets versions add \
    agskoleintra-message-handle-key \
    --project="${PROJECT_ID}" \
    --data-file=- >/dev/null
  unset generated_key
  echo "Added a new opaque message-handle key."
}

add_generated_message_handle_key_if_missing() {
  if gcloud secrets versions list agskoleintra-message-handle-key \
    --project="${PROJECT_ID}" \
    --filter='state=ENABLED' \
    --format='value(name)' 2>/dev/null | grep -q .; then
    echo "agskoleintra-message-handle-key already has an enabled version; leaving it unchanged."
    return
  fi
  add_generated_message_handle_key
}

add_secret_if_missing agskoleintra-school-username "SkoleIntra username"
add_secret_if_missing agskoleintra-school-password "SkoleIntra password"
if [[ "${ROTATE_OPENROUTER}" == "true" ]]; then
  add_secret agskoleintra-openrouter-api-key "OpenRouter API key"
else
  add_secret_if_missing agskoleintra-openrouter-api-key "OpenRouter API key"
fi
if [[ "${ROTATE_MESSAGE_HANDLE_KEY}" == "true" ]]; then
  add_generated_message_handle_key
else
  add_generated_message_handle_key_if_missing
fi
