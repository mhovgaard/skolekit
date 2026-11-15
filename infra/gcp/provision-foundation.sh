#!/usr/bin/env bash

set -euo pipefail

PROJECT_ID="${1:-}"
REGION="${2:-europe-west1}"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "Usage: $0 PROJECT_ID [REGION]" >&2
  exit 2
fi

ACTIVE_ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' | head -n 1)"
ACTIVE_PROJECT="$(gcloud config get-value project 2>/dev/null)"

if [[ -z "${ACTIVE_ACCOUNT}" ]]; then
  echo "No active gcloud account. Run: gcloud auth login" >&2
  exit 1
fi

if [[ "${ACTIVE_PROJECT}" != "${PROJECT_ID}" ]]; then
  echo "Refusing to provision ${PROJECT_ID}: active gcloud project is ${ACTIVE_PROJECT}." >&2
  echo "Select it first with: gcloud config set project ${PROJECT_ID}" >&2
  exit 1
fi

PROJECT_STATE="$(gcloud projects describe "${PROJECT_ID}" --format='value(lifecycleState)')"
BILLING_ENABLED="$(gcloud billing projects describe "${PROJECT_ID}" --format='value(billingEnabled)')"

if [[ "${PROJECT_STATE}" != "ACTIVE" ]]; then
  echo "Project ${PROJECT_ID} is not active (state: ${PROJECT_STATE})." >&2
  exit 1
fi

if [[ "${BILLING_ENABLED}" != "True" && "${BILLING_ENABLED}" != "true" ]]; then
  echo "Billing is not enabled for ${PROJECT_ID}." >&2
  exit 1
fi

echo "Provisioning ${PROJECT_ID} in ${REGION} as ${ACTIVE_ACCOUNT}"

APIS=(
  artifactregistry.googleapis.com
  cloudbuild.googleapis.com
  cloudscheduler.googleapis.com
  iam.googleapis.com
  iamcredentials.googleapis.com
  logging.googleapis.com
  monitoring.googleapis.com
  run.googleapis.com
  secretmanager.googleapis.com
  storage.googleapis.com
)

gcloud services enable "${APIS[@]}" --project="${PROJECT_ID}"

ensure_service_account() {
  local name="$1"
  local display_name="$2"
  local description="$3"
  local email="${name}@${PROJECT_ID}.iam.gserviceaccount.com"

  if ! gcloud iam service-accounts describe "${email}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud iam service-accounts create "${name}" \
      --project="${PROJECT_ID}" \
      --display-name="${display_name}" \
      --description="${description}"
  fi
}

ensure_service_account \
  ag-sync \
  "Skoleklar sync" \
  "Runs the private collection and manifest publication job"
ensure_service_account \
  ag-delivery \
  "Skoleklar delivery" \
  "Serves minimal manifests and stores iPhone receipts"
ensure_service_account \
  ag-scheduler \
  "Skoleklar scheduler" \
  "Invokes the sync job on the household schedule"

if ! gcloud artifacts repositories describe agskoleintra \
  --project="${PROJECT_ID}" \
  --location="${REGION}" >/dev/null 2>&1; then
  gcloud artifacts repositories create agskoleintra \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --repository-format=docker \
    --description="Skoleklar application images" \
    --immutable-tags \
    --labels=app=agskoleintra,environment=production
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRIVATE_BUCKET="${PROJECT_ID}-ag-private-state"
DELIVERY_BUCKET="${PROJECT_ID}-ag-delivery"

ensure_bucket() {
  local bucket="$1"
  local lifecycle_file="$2"
  local retention_period="${3:-}"

  if ! gcloud storage buckets describe "gs://${bucket}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    create_args=(
      "gs://${bucket}"
      "--project=${PROJECT_ID}"
      "--location=${REGION}"
      "--default-storage-class=STANDARD"
      "--uniform-bucket-level-access"
      "--public-access-prevention"
      "--soft-delete-duration=7d"
      "--lifecycle-file=${lifecycle_file}"
    )
    if [[ -n "${retention_period}" ]]; then
      create_args+=("--retention-period=${retention_period}")
    fi
    gcloud storage buckets create "${create_args[@]}"
  fi

  update_args=(
    "gs://${bucket}"
    "--project=${PROJECT_ID}"
    "--uniform-bucket-level-access"
    "--public-access-prevention"
    "--soft-delete-duration=7d"
    "--lifecycle-file=${lifecycle_file}"
    "--update-labels=app=agskoleintra,environment=production,data_classification=private"
  )
  if [[ -n "${retention_period}" ]]; then
    update_args+=("--retention-period=${retention_period}")
  fi
  gcloud storage buckets update "${update_args[@]}"

  gcloud storage buckets update "gs://${bucket}" \
    --project="${PROJECT_ID}" \
    --versioning
}

ensure_bucket \
  "${PRIVATE_BUCKET}" \
  "${SCRIPT_DIR}/private-state-lifecycle.json"
ensure_bucket \
  "${DELIVERY_BUCKET}" \
  "${SCRIPT_DIR}/delivery-lifecycle.json" \
  7d

SYNC_SA="ag-sync@${PROJECT_ID}.iam.gserviceaccount.com"
DELIVERY_SA="ag-delivery@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud storage buckets add-iam-policy-binding "gs://${PRIVATE_BUCKET}" \
  --member="serviceAccount:${SYNC_SA}" \
  --role=roles/storage.objectAdmin >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://${DELIVERY_BUCKET}" \
  --member="serviceAccount:${SYNC_SA}" \
  --role=roles/storage.objectAdmin >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://${DELIVERY_BUCKET}" \
  --member="serviceAccount:${DELIVERY_SA}" \
  --role=roles/storage.objectViewer >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://${DELIVERY_BUCKET}" \
  --member="serviceAccount:${DELIVERY_SA}" \
  --role=roles/storage.objectCreator >/dev/null

SECRETS=(
  agskoleintra-school-username
  agskoleintra-school-password
  agskoleintra-openrouter-api-key
  agskoleintra-message-handle-key
  agskoleintra-device-token-hash
)

for secret in "${SECRETS[@]}"; do
  if ! gcloud secrets describe "${secret}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud secrets create "${secret}" \
      --project="${PROJECT_ID}" \
      --replication-policy=user-managed \
      --locations="${REGION}" \
      --labels=app=agskoleintra,environment=production
  fi
done

for secret in \
  agskoleintra-school-username \
  agskoleintra-school-password \
  agskoleintra-openrouter-api-key \
  agskoleintra-message-handle-key; do
  gcloud secrets add-iam-policy-binding "${secret}" \
    --project="${PROJECT_ID}" \
    --member="serviceAccount:${SYNC_SA}" \
    --role=roles/secretmanager.secretAccessor >/dev/null
done

if gcloud secrets describe agskoleintra-openai-api-key \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  if gcloud secrets get-iam-policy agskoleintra-openai-api-key \
    --project="${PROJECT_ID}" \
    --flatten='bindings[].members' \
    --filter="bindings.role=roles/secretmanager.secretAccessor AND bindings.members=serviceAccount:${SYNC_SA}" \
    --format='value(bindings.members)' 2>/dev/null | grep -q .; then
    gcloud secrets remove-iam-policy-binding agskoleintra-openai-api-key \
      --project="${PROJECT_ID}" \
      --member="serviceAccount:${SYNC_SA}" \
      --role=roles/secretmanager.secretAccessor >/dev/null
  fi
fi

gcloud secrets add-iam-policy-binding agskoleintra-device-token-hash \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${DELIVERY_SA}" \
  --role=roles/secretmanager.secretAccessor >/dev/null

echo "Foundation provisioning complete."
echo "Private state: gs://${PRIVATE_BUCKET}"
echo "Delivery:      gs://${DELIVERY_BUCKET}"
echo "Images:        ${REGION}-docker.pkg.dev/${PROJECT_ID}/agskoleintra"
