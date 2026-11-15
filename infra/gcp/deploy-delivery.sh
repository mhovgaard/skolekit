#!/usr/bin/env bash

set -euo pipefail

PROJECT_ID="${1:-}"
REGION="${2:-europe-west1}"
IMAGE="${3:-}"

if [[ -z "${PROJECT_ID}" || -z "${IMAGE}" ]]; then
  echo "Usage: $0 PROJECT_ID REGION IMAGE" >&2
  exit 2
fi

if [[ "$(gcloud config get-value project 2>/dev/null)" != "${PROJECT_ID}" ]]; then
  echo "Refusing to deploy to a project that is not active in gcloud." >&2
  exit 1
fi

gcloud run deploy agskoleintra-delivery \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --service-account="ag-delivery@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-env-vars="AG_DELIVERY_BUCKET=${PROJECT_ID}-ag-delivery,AG_DEVICE_ID=family-primary-iphone,AG_DISPLAY_BUCKET=${PROJECT_ID}-ag-display-current,AG_CONTROL_BUCKET=${PROJECT_ID}-ag-control,AG_REFRESH_JOB=projects/${PROJECT_ID}/locations/${REGION}/jobs/agskoleintra-sync" \
  --set-secrets="AG_DEVICE_TOKEN_HASH=agskoleintra-device-token-hash:latest,AG_DEVICE_REGISTRY=agskoleintra-device-registry:latest" \
  --no-allow-unauthenticated \
  --invoker-iam-check \
  --ingress=all \
  --port=8080 \
  --cpu=1 \
  --memory=512Mi \
  --concurrency=20 \
  --min=0 \
  --max=1 \
  --min-instances=0 \
  --max-instances=1 \
  --timeout=30s \
  --quiet

GATEWAY_SA="ag-gateway@${PROJECT_ID}.iam.gserviceaccount.com"
if gcloud iam service-accounts describe "${GATEWAY_SA}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud run services add-iam-policy-binding agskoleintra-delivery \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --member="serviceAccount:${GATEWAY_SA}" \
    --role=roles/run.invoker >/dev/null
fi

gcloud run services describe agskoleintra-delivery \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format='value(status.url)'
