#!/usr/bin/env bash

set -euo pipefail

PROJECT_ID="${1:-}"
REGION="${2:-europe-west1}"
IMAGE_TAG="${3:-}"

if [[ -z "${PROJECT_ID}" || -z "${IMAGE_TAG}" ]]; then
  echo "Usage: $0 PROJECT_ID REGION UNIQUE_IMAGE_TAG" >&2
  exit 2
fi

if [[ "$(gcloud config get-value project 2>/dev/null)" != "${PROJECT_ID}" ]]; then
  echo "Refusing to build for a project that is not active in gcloud." >&2
  exit 1
fi

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/agskoleintra/app:${IMAGE_TAG}"
gcloud builds submit . \
  --project="${PROJECT_ID}" \
  --tag="${IMAGE}" \
  --quiet

echo "${IMAGE}"
