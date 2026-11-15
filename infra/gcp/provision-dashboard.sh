#!/usr/bin/env bash
set -euo pipefail
PROJECT_ID="${1:?project required}"
REGION="${2:-europe-west1}"
[[ "$(gcloud config get-value project 2>/dev/null)" == "${PROJECT_ID}" ]]
for suffix in ag-control ag-display-current; do
  bucket="gs://${PROJECT_ID}-${suffix}"
  if ! gcloud storage buckets describe "${bucket}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud storage buckets create "${bucket}" --project="${PROJECT_ID}" --location="${REGION}" --uniform-bucket-level-access --public-access-prevention --soft-delete-duration=0
  fi
  gcloud storage buckets update "${bucket}" --no-versioning --soft-delete-duration=0
  for identity in ag-sync ag-delivery; do
    role=roles/storage.objectAdmin
    if [[ "${suffix}" == ag-display-current && "${identity}" == ag-delivery ]]; then role=roles/storage.objectViewer; fi
    gcloud storage buckets add-iam-policy-binding "${bucket}" --member="serviceAccount:${identity}@${PROJECT_ID}.iam.gserviceaccount.com" --role="${role}" >/dev/null
  done
done
gcloud storage buckets add-iam-policy-binding "gs://${PROJECT_ID}-ag-display-current" --member="serviceAccount:ag-sync@${PROJECT_ID}.iam.gserviceaccount.com" --role=roles/storage.legacyBucketReader >/dev/null
gcloud storage buckets update "gs://${PROJECT_ID}-ag-display-current" --lifecycle-file="$(dirname "$0")/display-lifecycle.json"
# Allows invocation only of this job; no overrides or scheduler creation. The buckets
# above belong to the foundation and this grant to the job, so the script may run
# before the job exists; run it again after deploy-sync-job.sh for the grant.
if gcloud run jobs describe agskoleintra-sync --project="${PROJECT_ID}" --region="${REGION}" >/dev/null 2>&1; then
  gcloud run jobs add-iam-policy-binding agskoleintra-sync --project="${PROJECT_ID}" --region="${REGION}" --member="serviceAccount:ag-delivery@${PROJECT_ID}.iam.gserviceaccount.com" --role=roles/run.invoker >/dev/null
else
  echo "The job does not exist yet: run this script again after deploy-sync-job.sh to let the phone start a scan." >&2
fi
# Read operation status at project scope (operations are regional resources).
gcloud iam roles create agRefreshStatus --project="${PROJECT_ID}" --title='Read refresh operation status' --permissions=run.operations.get --stage=GA 2>/dev/null || gcloud iam roles describe agRefreshStatus --project="${PROJECT_ID}" >/dev/null
gcloud projects add-iam-policy-binding "${PROJECT_ID}" --member="serviceAccount:ag-delivery@${PROJECT_ID}.iam.gserviceaccount.com" --role="projects/${PROJECT_ID}/roles/agRefreshStatus" --condition=None >/dev/null
