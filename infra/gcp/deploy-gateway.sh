#!/usr/bin/env bash

set -euo pipefail

PROJECT_ID="${1:-}"
REGION="${2:-europe-west1}"
API_ID="agskoleintra-delivery"
GATEWAY_ID="agskoleintra-delivery"
GATEWAY_SA="ag-gateway@${PROJECT_ID}.iam.gserviceaccount.com"
KEY_DISPLAY_NAME="Skoleklar primary iPhone gateway"
KEY_ID="agskoleintra-iphone-gateway-v2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "Usage: $0 PROJECT_ID [REGION]" >&2
  exit 2
fi

if [[ "$(gcloud config get-value project 2>/dev/null)" != "${PROJECT_ID}" ]]; then
  echo "Refusing to deploy to a project that is not active in gcloud." >&2
  exit 1
fi

gcloud services enable \
  apigateway.googleapis.com \
  apikeys.googleapis.com \
  servicemanagement.googleapis.com \
  servicecontrol.googleapis.com \
  --project="${PROJECT_ID}"

if ! gcloud iam service-accounts describe "${GATEWAY_SA}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create ag-gateway \
    --project="${PROJECT_ID}" \
    --display-name="Skoleklar API Gateway" \
    --description="Invokes the private iPhone delivery backend"
fi

gcloud run services add-iam-policy-binding agskoleintra-delivery \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --member="serviceAccount:${GATEWAY_SA}" \
  --role=roles/run.invoker >/dev/null

BACKEND_URL="$(gcloud run services describe agskoleintra-delivery \
  --project="${PROJECT_ID}" --region="${REGION}" \
  --format='value(status.url)')"
if [[ -z "${BACKEND_URL}" ]]; then
  echo "Cloud Run delivery service has no ready URL." >&2
  exit 1
fi

RENDER_DIR="$(mktemp -d)"
RENDERED_SPEC="${RENDER_DIR}/gateway-openapi.yaml"
cleanup() {
  unlink "${RENDERED_SPEC}" 2>/dev/null || true
  rmdir "${RENDER_DIR}" 2>/dev/null || true
}
trap cleanup EXIT
sed "s|__BACKEND_URL__|${BACKEND_URL}|g" \
  "${SCRIPT_DIR}/gateway-openapi.yaml" >"${RENDERED_SPEC}"

if ! gcloud api-gateway apis describe "${API_ID}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud api-gateway apis create "${API_ID}" --project="${PROJECT_ID}"
fi

CONFIG_ID="delivery-$(date -u +%Y%m%d-%H%M%S)"
gcloud api-gateway api-configs create "${CONFIG_ID}" \
  --api="${API_ID}" \
  --openapi-spec="${RENDERED_SPEC}" \
  --backend-auth-service-account="${GATEWAY_SA}" \
  --project="${PROJECT_ID}" \
  --quiet

if gcloud api-gateway gateways describe "${GATEWAY_ID}" \
  --project="${PROJECT_ID}" --location="${REGION}" >/dev/null 2>&1; then
  gcloud api-gateway gateways update "${GATEWAY_ID}" \
    --api="${API_ID}" \
    --api-config="${CONFIG_ID}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --async \
    --quiet
else
  gcloud api-gateway gateways create "${GATEWAY_ID}" \
    --api="${API_ID}" \
    --api-config="${CONFIG_ID}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --async \
    --quiet
fi

for _ in $(seq 1 60); do
  GATEWAY_STATE="$(gcloud api-gateway gateways describe "${GATEWAY_ID}" \
    --project="${PROJECT_ID}" --location="${REGION}" \
    --format='value(state)' 2>/dev/null || true)"
  if [[ "${GATEWAY_STATE}" == "ACTIVE" ]]; then
    break
  fi
  if [[ "${GATEWAY_STATE}" == "FAILED" ]]; then
    echo "API Gateway entered FAILED state." >&2
    exit 1
  fi
  sleep 10
done
if [[ "${GATEWAY_STATE:-}" != "ACTIVE" ]]; then
  echo "API Gateway did not become active within ten minutes." >&2
  exit 1
fi

MANAGED_SERVICE="$(gcloud api-gateway apis describe "${API_ID}" \
  --project="${PROJECT_ID}" --format='value(managedService)')"
gcloud services enable "${MANAGED_SERVICE}" --project="${PROJECT_ID}"

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
KEY_NAME="projects/${PROJECT_NUMBER}/locations/global/keys/${KEY_ID}"
if ! gcloud services api-keys describe "${KEY_NAME}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  if ! gcloud services api-keys create \
    --project="${PROJECT_ID}" \
    --key-id="${KEY_ID}" \
    --display-name="${KEY_DISPLAY_NAME}" \
    --api-target="service=${MANAGED_SERVICE}" \
    --quiet >/dev/null 2>&1; then
    echo "Gateway API key creation failed." >&2
    exit 1
  fi
else
  if ! gcloud services api-keys update "${KEY_NAME}" \
    --project="${PROJECT_ID}" \
    --api-target="service=${MANAGED_SERVICE}" \
    --quiet >/dev/null 2>&1; then
    echo "Gateway API key restriction update failed." >&2
    exit 1
  fi
fi

GATEWAY_KEY="$(gcloud services api-keys get-key-string "${KEY_NAME}" \
  --project="${PROJECT_ID}" --format='value(keyString)')"
printf %s "${GATEWAY_KEY}" | uv run python -c \
  'import sys; from skoleklar.device_token import store_gateway_api_key; store_gateway_api_key(sys.stdin.read())'
unset GATEWAY_KEY

GATEWAY_HOST="$(gcloud api-gateway gateways describe "${GATEWAY_ID}" \
  --project="${PROJECT_ID}" --location="${REGION}" \
  --format='value(defaultHostname)')"
echo "Gateway ready: https://${GATEWAY_HOST}"
echo "Gateway API key stored in macOS Keychain account family-primary-iphone-gateway-api-key."
echo "Cloud Run remains private; only the gateway identity can invoke the device service."
