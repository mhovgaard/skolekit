#!/usr/bin/env bash

set -euo pipefail

PROJECT_ID="${1:-}"
REGION="${2:-europe-west1}"
IMAGE="${3:-}"
MESSAGE_MODE="${4:-off}"
MESSAGE_PROCESSOR="${5:-disabled}"
# Opt-in, chosen at setup: 'model' lets the model name each latest message's topic
# in one or two words for the parents' board (zdr processor only). Default off.
MESSAGE_TOPICS="${6:-off}"
WEATHER_MODE="${AG_WEATHER_MODE:-family}"
# The household's model, timezone and weather place come from its skoleklar.toml.
source "$(dirname "$0")/household-settings.sh"

if [[ -z "${PROJECT_ID}" || -z "${IMAGE}" ]]; then
  echo "Usage: $0 PROJECT_ID REGION IMAGE [off|events-only] [disabled|zdr] [off|model]" >&2
  exit 2
fi

case "${MESSAGE_MODE}" in
  legacy)
    echo "The legacy message mode was removed: message text is never stored." >&2
    exit 2
    ;;
  off)
    if [[ "${MESSAGE_PROCESSOR}" != "disabled" ]]; then
      echo "${MESSAGE_MODE} mode requires the disabled message processor." >&2
      exit 2
    fi
    ;;
  events-only)
    if [[ "${MESSAGE_PROCESSOR}" != "zdr" ]]; then
      echo "Cloud events-only mode requires the explicit zdr processor." >&2
      exit 2
    fi
    ;;
  *)
    echo "Unknown message mode: ${MESSAGE_MODE}" >&2
    exit 2
    ;;
esac

case "${MESSAGE_TOPICS}" in
  off) ;;
  model)
    if [[ "${MESSAGE_PROCESSOR}" != "zdr" ]]; then
      echo "Message topics need the explicit zdr processor." >&2
      exit 2
    fi
    ;;
  *)
    echo "Unknown message topic mode: ${MESSAGE_TOPICS}" >&2
    exit 2
    ;;
esac

if [[ "$(gcloud config get-value project 2>/dev/null)" != "${PROJECT_ID}" ]]; then
  echo "Refusing to deploy to a project that is not active in gcloud." >&2
  exit 1
fi

for secret in \
  agskoleintra-school-username \
  agskoleintra-school-password \
  agskoleintra-openrouter-api-key \
  agskoleintra-message-handle-key; do
  if ! gcloud secrets versions list "${secret}" \
    --project="${PROJECT_ID}" \
    --filter='state=ENABLED' \
    --format='value(name)' | grep -q .; then
    echo "Secret ${secret} has no enabled value; sync-job deployment stopped." >&2
    exit 1
  fi
done

# No place configured: no forecast. The place label must not contain a comma.
if [[ -n "${HOUSEHOLD_WEATHER_PLACE}" ]]; then
  if [[ "${HOUSEHOLD_WEATHER_PLACE}" == *,* ]]; then
    echo "The weather place must not contain a comma." >&2
    exit 2
  fi
  WEATHER_VARS="AG_WEATHER_PLACE=${HOUSEHOLD_WEATHER_PLACE},AG_WEATHER_LATITUDE=${HOUSEHOLD_WEATHER_LATITUDE},AG_WEATHER_LONGITUDE=${HOUSEHOLD_WEATHER_LONGITUDE},AG_WEATHER_MODE=${WEATHER_MODE}"
else
  WEATHER_VARS="AG_WEATHER=off"
fi
MODEL_VAR=""
if [[ -n "${HOUSEHOLD_MODEL}" ]]; then
  MODEL_VAR=",AG_LLM_MODEL=${HOUSEHOLD_MODEL}"
fi

gcloud run jobs deploy agskoleintra-sync \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --service-account="ag-sync@${PROJECT_ID}.iam.gserviceaccount.com" \
  --command=python \
  --args=-m,skoleklar.cloud_job \
  --set-env-vars="AG_PRIVATE_BUCKET=${PROJECT_ID}-ag-private-state,AG_DELIVERY_BUCKET=${PROJECT_ID}-ag-delivery,AG_CONTROL_BUCKET=${PROJECT_ID}-ag-control,AG_DISPLAY_BUCKET=${PROJECT_ID}-ag-display-current,AG_TIMEZONE=${HOUSEHOLD_TIMEZONE}${MODEL_VAR},AG_SCHOOL_MESSAGE_MODE=${MESSAGE_MODE},AG_SCHOOL_MESSAGE_PROCESSOR=${MESSAGE_PROCESSOR},AG_MESSAGE_TOPICS=${MESSAGE_TOPICS},${WEATHER_VARS}" \
  --set-secrets="AG_SCHOOL_USERNAME=agskoleintra-school-username:latest,AG_SCHOOL_PASSWORD=agskoleintra-school-password:latest,OPENROUTER_API_KEY=agskoleintra-openrouter-api-key:latest,AG_SCHOOL_MESSAGE_HANDLE_KEY=agskoleintra-message-handle-key:latest" \
  --cpu=1 \
  --memory=2Gi \
  --task-timeout=30m \
  --max-retries=1 \
  --tasks=1 \
  --parallelism=1 \
  --quiet

# Manual-only: this helper does not create or enable scheduler triggers.
