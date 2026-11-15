#!/usr/bin/env bash

set -euo pipefail

PROJECT_ID="${1:-}"
STATE_ROOT="${AG_SCHOOL_HOME:-${HOME}/.config/ag-skoleintra}"
BUCKET="${PROJECT_ID}-ag-private-state"
REPLACE="false"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "Usage: $0 PROJECT_ID [--restricted] [--replace]" >&2
  exit 2
fi

shift
for option in "$@"; do
  case "${option}" in
    --replace) REPLACE="true" ;;
    # The restricted database is the only kind since the legacy message mode
    # was removed (14 November 2026); the flag stays for older instructions.
    --restricted) ;;
    *)
      echo "Unknown option: ${option}" >&2
      exit 2
      ;;
  esac
done

DATABASE_FILENAME="school-restricted.db"
DATABASE_PREFIX="state/restricted-v1"

if [[ "$(gcloud config get-value project 2>/dev/null)" != "${PROJECT_ID}" ]]; then
  echo "Refusing to seed a project that is not active in gcloud." >&2
  exit 1
fi

if [[ ! -f "${STATE_ROOT}/${DATABASE_FILENAME}" || ! -f "${STATE_ROOT}/auth-state.json" ]]; then
  echo "Local ${DATABASE_FILENAME} and auth-state.json are required under ${STATE_ROOT}." >&2
  exit 1
fi

if [[ "$(sqlite3 "${STATE_ROOT}/${DATABASE_FILENAME}" 'PRAGMA integrity_check;')" != "ok" ]]; then
  echo "Local SQLite integrity check failed." >&2
  exit 1
fi

upload_state_file() {
  local filename="$1"
  local prefix="$2"
  local object="gs://${BUCKET}/${prefix}/${filename}"
  local generation
  local generation_match
  generation="$(gcloud storage objects describe "${object}" \
    --project="${PROJECT_ID}" \
    --format='value(generation)' 2>/dev/null || true)"
  if [[ -n "${generation}" && "${REPLACE}" != "true" ]]; then
    echo "Remote ${prefix}/${filename} already exists; refusing to overwrite it." >&2
    exit 1
  fi
  if [[ -n "${generation}" ]]; then
    generation_match="${generation}"
  else
    generation_match="0"
  fi
  gcloud storage cp "${STATE_ROOT}/${filename}" "${object}" \
    --project="${PROJECT_ID}" \
    --if-generation-match="${generation_match}" >/dev/null
}

upload_state_file "${DATABASE_FILENAME}" "${DATABASE_PREFIX}"

AUTH_OBJECT="gs://${BUCKET}/state/auth-state.json"
AUTH_GENERATION="$(gcloud storage objects describe "${AUTH_OBJECT}" \
  --project="${PROJECT_ID}" \
  --format='value(generation)' 2>/dev/null || true)"
if [[ -z "${AUTH_GENERATION}" || "${REPLACE}" == "true" ]]; then
  upload_state_file "auth-state.json" "state"
fi

if [[ "${REPLACE}" == "true" ]]; then
  echo "Replaced remote state from the verified local baseline using generation preconditions."
  echo "Prior object generations remain recoverable through bucket versioning."
else
  echo "Seeded the verified database and browser session into private Cloud Storage."
fi
