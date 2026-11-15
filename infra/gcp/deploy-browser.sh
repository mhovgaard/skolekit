#!/usr/bin/env bash
set -euo pipefail
PROJECT_ID="${1:?project required}"
REGION="${2:?region required}"
IMAGE="${3:?image required}"
# The owners and viewers are the household's ([gcp] owners, viewers); an address given
# here is used only when the file names no owner.
source "$(dirname "$0")/household-settings.sh"
OWNERS="${HOUSEHOLD_OWNERS:-${4:-}}"
VIEWERS="${HOUSEHOLD_VIEWERS:-}"
if [[ -z "${OWNERS}" ]]; then
  echo "Name the owners in skoleklar.toml ([gcp] owners) or pass one address." >&2
  exit 2
fi
[[ "$(gcloud config get-value project 2>/dev/null)" == "${PROJECT_ID}" ]]
NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
SA="ag-browser@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud services enable iap.googleapis.com --project="${PROJECT_ID}"
if ! gcloud iam service-accounts describe "${SA}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create ag-browser --project="${PROJECT_ID}" --display-name='Private school dashboard'
fi
gcloud storage buckets add-iam-policy-binding "gs://${PROJECT_ID}-ag-display-current" --member="serviceAccount:${SA}" --role=roles/storage.objectViewer >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://${PROJECT_ID}-ag-control" --member="serviceAccount:${SA}" --role=roles/storage.objectAdmin >/dev/null
gcloud run jobs add-iam-policy-binding agskoleintra-sync --project="${PROJECT_ID}" --region="${REGION}" --member="serviceAccount:${SA}" --role=roles/run.invoker >/dev/null
gcloud projects add-iam-policy-binding "${PROJECT_ID}" --member="serviceAccount:${SA}" --role="projects/${PROJECT_ID}/roles/agRefreshStatus" --condition=None >/dev/null
gcloud alpha run deploy agskoleintra-dashboard --project="${PROJECT_ID}" --region="${REGION}" --image="${IMAGE}" --service-account="${SA}" \
  --command=uvicorn --args=skoleklar.browser_api:app,--host,0.0.0.0,--port,8080,--no-access-log \
  --set-env-vars="^;^AG_DISPLAY_BUCKET=${PROJECT_ID}-ag-display-current;AG_CONTROL_BUCKET=${PROJECT_ID}-ag-control;AG_REFRESH_JOB=projects/${PROJECT_ID}/locations/${REGION}/jobs/agskoleintra-sync;AG_IAP_AUDIENCE=/projects/${NUMBER}/locations/${REGION}/services/agskoleintra-dashboard;AG_OWNERS=${OWNERS};AG_VIEWERS=${VIEWERS:-${OWNERS}}" \
  --no-allow-unauthenticated --invoker-iam-check --iap --min=0 --max=1 --memory=512Mi --timeout=30s --quiet
# IAP exposes the stable project-number URL to browsers. Cloud Run's status.url
# may still return the legacy hash-based a.run.app alias, which produces a
# different Origin header and causes same-origin refresh requests to fail.
URL="https://agskoleintra-dashboard-${NUMBER}.${REGION}.run.app"
ALIAS_URL="$(gcloud run services describe agskoleintra-dashboard --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"
ORIGINS="${URL}"
if [[ "${ALIAS_URL}" != "${URL}" ]]; then
  ORIGINS="${URL}|${ALIAS_URL}"
fi
gcloud run services update agskoleintra-dashboard --project="${PROJECT_ID}" --region="${REGION}" \
  --update-env-vars="AG_BROWSER_ORIGIN=${URL},AG_BROWSER_ORIGINS=${ORIGINS}" --quiet
gcloud run services add-iam-policy-binding agskoleintra-dashboard --project="${PROJECT_ID}" --region="${REGION}" --member="serviceAccount:service-${NUMBER}@gcp-sa-iap.iam.gserviceaccount.com" --role=roles/run.invoker >/dev/null
IFS=',' read -r -a PEOPLE <<<"${OWNERS},${VIEWERS}"
for person in "${PEOPLE[@]}"; do
  [[ -z "${person}" ]] && continue
  gcloud alpha iap web add-iam-policy-binding --project="${PROJECT_ID}" --resource-type=cloud-run --service=agskoleintra-dashboard --region="${REGION}" --member="user:${person}" --role=roles/iap.httpsResourceAccessor >/dev/null
done
printf '%s\n' "${URL}"
