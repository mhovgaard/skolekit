#!/usr/bin/env bash

set -euo pipefail

PROJECT_ID="${1:-}"
REGION="${2:-europe-west1}"
SCHEDULER_SA="ag-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"
RUN_URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/agskoleintra-sync:run"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "Usage: $0 PROJECT_ID [REGION]" >&2
  exit 2
fi

if [[ "$(gcloud config get-value project 2>/dev/null)" != "${PROJECT_ID}" ]]; then
  echo "Refusing to schedule a project that is not active in gcloud." >&2
  exit 1
fi

if ! gcloud run jobs describe agskoleintra-sync \
  --project="${PROJECT_ID}" --region="${REGION}" >/dev/null 2>&1; then
  echo "Deploy and manually verify agskoleintra-sync before scheduling it." >&2
  exit 1
fi

source "$(dirname "$0")/household-settings.sh"

# The scheduler's identity may run this job and nothing else.
gcloud run jobs add-iam-policy-binding agskoleintra-sync \
  --project="${PROJECT_ID}" --region="${REGION}" \
  --member="serviceAccount:${SCHEDULER_SA}" --role=roles/run.invoker >/dev/null

ensure_schedule() {
  local name="$1"
  local schedule="$2"
  if gcloud scheduler jobs describe "${name}" \
    --project="${PROJECT_ID}" --location="${REGION}" >/dev/null 2>&1; then
    gcloud scheduler jobs update http "${name}" \
      --project="${PROJECT_ID}" \
      --location="${REGION}" \
      --schedule="${schedule}" \
      --time-zone="${HOUSEHOLD_TIMEZONE}" \
      --uri="${RUN_URI}" \
      --http-method=POST \
      --oauth-service-account-email="${SCHEDULER_SA}" \
      --quiet
  else
    gcloud scheduler jobs create http "${name}" \
      --project="${PROJECT_ID}" \
      --location="${REGION}" \
      --schedule="${schedule}" \
      --time-zone="${HOUSEHOLD_TIMEZONE}" \
      --uri="${RUN_URI}" \
      --http-method=POST \
      --oauth-service-account-email="${SCHEDULER_SA}" \
      --quiet
  fi
}

# Scans only. Each run draws every layout change for the next 36 hours (the
# children's view 06–09 and 15–20, the weekend cover, Saturday 18:00, Sunday
# noon), so none needs its own trigger. The times are the household's
# ([runtime] scans; the default is 05:30 every day). The daily scan takes over the
# old morning schedule's name, so a live project gets no second one; any other time
# is named after itself. The longest gap must stay inside the board's 36-hour
# expiry. The board's firmware checks at 06:00 for the 05:30 scan (wake_plan.h).
declare -a WANTED=()
while IFS='|' read -r cron text; do
  [[ -z "${cron}" ]] && continue
  case "${cron}" in
    "30 5 * * *" | "45 5 * * 1-5") name=agskoleintra-morning ;;
    "30 12 * * *") name=agskoleintra-midday ;;
    "30 19 * * *") name=agskoleintra-evening ;;
    *) name="agskoleintra-scan-$(tr -c 'a-z0-9' '-' <<<"${text}" | tr -s '-' | sed 's/-$//')" ;;
  esac
  ensure_schedule "${name}" "${cron}"
  WANTED+=("${name}")
done <<<"${HOUSEHOLD_SCANS}"

# Never deletes: a schedule that is no longer wanted is only named, for the parent to pause.
for existing in $(gcloud scheduler jobs list --project="${PROJECT_ID}" --location="${REGION}" \
    --format='value(ID)' --filter='name~agskoleintra-'); do
  if [[ " ${WANTED[*]} " != *" ${existing##*/} "* ]]; then
    echo "Not in skoleklar.toml, left as it is: ${existing##*/} (pause it if it should not run)." >&2
  fi
done
