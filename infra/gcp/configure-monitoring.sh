#!/usr/bin/env bash

set -euo pipefail

PROJECT_ID="${1:-}"
ALERT_EMAIL="${2:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${PROJECT_ID}" || -z "${ALERT_EMAIL}" ]]; then
  echo "Usage: $0 PROJECT_ID ALERT_EMAIL" >&2
  exit 2
fi
if [[ "$(gcloud config get-value project 2>/dev/null)" != "${PROJECT_ID}" ]]; then
  echo "Refusing to configure a project that is not active in gcloud." >&2
  exit 1
fi

ACCESS_TOKEN="$(gcloud auth print-access-token)"
cleanup() { unset ACCESS_TOKEN; }
trap cleanup EXIT
AUTH_HEADER="Authorization: Bearer ${ACCESS_TOKEN}"
BASE_URL="https://monitoring.googleapis.com/v3/projects/${PROJECT_ID}"

CHANNELS="$(curl --fail-with-body --silent --show-error \
  --header "${AUTH_HEADER}" "${BASE_URL}/notificationChannels")"
CHANNEL_NAME="$(printf %s "${CHANNELS}" | jq -r --arg email "${ALERT_EMAIL}" \
  '.notificationChannels[]? | select(.type == "email" and .labels.email_address == $email) | .name' | head -n 1)"
if [[ -z "${CHANNEL_NAME}" ]]; then
  CHANNEL_BODY="$(jq -nc --arg email "${ALERT_EMAIL}" \
    '{type:"email",displayName:"Skoleklar security alerts",labels:{email_address:$email},enabled:true}')"
  CHANNEL_NAME="$(curl --fail-with-body --silent --show-error --request POST \
    --header "${AUTH_HEADER}" --header 'Content-Type: application/json' \
    --data "${CHANNEL_BODY}" "${BASE_URL}/notificationChannels" | jq -r '.name')"
fi

POLICIES="$(curl --fail-with-body --silent --show-error \
  --header "${AUTH_HEADER}" "${BASE_URL}/alertPolicies")"
POLICY_NAME="$(printf %s "${POLICIES}" | jq -r \
  '.alertPolicies[]? | select(.displayName == "AG delivery unusual backend request volume") | .name' | head -n 1)"
if [[ -z "${POLICY_NAME}" ]]; then
  POLICY_BODY="$(jq --arg channel "${CHANNEL_NAME}" \
    '.notificationChannels = [$channel]' "${SCRIPT_DIR}/delivery-abuse-alert.json")"
  POLICY_NAME="$(curl --fail-with-body --silent --show-error --request POST \
    --header "${AUTH_HEADER}" --header 'Content-Type: application/json' \
    --data "${POLICY_BODY}" "${BASE_URL}/alertPolicies" | jq -r '.name')"
fi

echo "Notification channel: ${CHANNEL_NAME}"
echo "Alert policy: ${POLICY_NAME}"
