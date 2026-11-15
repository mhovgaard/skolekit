# Target state on Google Cloud

This is what a household's Skoleklar project looks like when it is fully
built. The [skill](SKILL.md) gives the order to build it in. The scripts in this
folder do the building.

For builders provisioning a project and agents auditing one. These are target
settings derived from the scripts, not a claim about a particular live project.
Replace `PROJECT` and `REGION` with the installation's values and verify every
resource before treating setup as complete.

Each resource belongs to one module. A household builds `core` and picks the
others:

| Module | What it gives | Needs |
| --- | --- | --- |
| `core` | The scheduled scans and the private state | — |
| `phone` | The iPhone manifest, through API Gateway | `core` |
| `browser` | The board in a browser, behind IAP | `core` |
| `eink` | The FireBeetle board | `phone` (the gateway and device registry) |
| `alerts` | An email when the gateway sees unusual traffic | `phone` |

Every check below is read-only. A secret check only counts enabled versions;
it never reads a value.

## Project

- A dedicated project with billing enabled. Use it only for this installation
  so its IAM and usage are independently understandable.
- Use the selected region for storage and secrets. Model requests cross a separate
  provider boundary; region selection is not a data-residency guarantee.
- Labels `app=agskoleintra,environment=production` on buckets and secrets.

APIs:

| Module | APIs | Enabled by |
| --- | --- | --- |
| `core` | `artifactregistry`, `cloudbuild`, `cloudscheduler`, `iam`, `iamcredentials`, `logging`, `monitoring`, `run`, `secretmanager`, `storage` | `provision-foundation.sh` |
| `phone` | `apigateway`, `apikeys`, `servicemanagement`, `servicecontrol`, and the gateway's own managed service | `deploy-gateway.sh` |
| `browser` | `iap` | `deploy-browser.sh` |

```bash
gcloud services list --enabled --project=PROJECT --format='value(config.name)'
```

## Identities

| Account | Module | Runs | Can do |
| --- | --- | --- | --- |
| `ag-sync` | `core` | the scan job | admin on objects in all four buckets; read the display bucket's listing; read the school username and password, the OpenRouter key and the message-handle key |
| `ag-scheduler` | `core` | the schedules | invoke the scan job |
| `ag-delivery` | `phone` | the delivery service | read and create objects in the delivery bucket (it cannot update or delete them); admin on control objects; read display objects; read the device-token hash and the device registry; invoke the scan job; read refresh status |
| `ag-gateway` | `phone` | API Gateway's calls | invoke the delivery service |
| `ag-browser` | `browser` | the dashboard service | read display objects; admin on control objects; invoke the scan job; read refresh status |
| IAP service agent `service-NUMBER@gcp-sa-iap` | `browser` | IAP | invoke the dashboard service |

The custom role `agRefreshStatus` holds one permission, `run.operations.get`.
It is granted at project level, because Cloud Run operations are regional
resources.

Nothing has a project-wide data role. Every grant is on one bucket, one secret,
one job or one service.

```bash
gcloud iam service-accounts list --project=PROJECT --format='value(email)'
gcloud run jobs get-iam-policy agskoleintra-sync --project=PROJECT --region=REGION
gcloud run services get-iam-policy agskoleintra-delivery --project=PROJECT --region=REGION
gcloud run services get-iam-policy agskoleintra-dashboard --project=PROJECT --region=REGION
gcloud projects get-iam-policy PROJECT --flatten=bindings --filter='bindings.members~ag-'
gcloud storage buckets get-iam-policy gs://PROJECT-ag-private-state   # and the other three
```

## Buckets

All four use uniform bucket-level access and public access prevention.

| Bucket | Module | Versioning | Soft delete | Retention | Lifecycle | Holds |
| --- | --- | --- | --- | --- | --- | --- |
| `PROJECT-ag-private-state` | `core` | on | 7 days | — | old versions go after 30 days | the database (`state/restricted-v1/school-restricted.db`), the school session (`state/auth-state.json`), the household's config when there is one (`state/school.toml`, `state/profile.toml` and `state/skoleklar.toml`, `state/triage.toml`, and its own house pictures in `state/artwork/`; read by the scan, never written by it), and the board's saved state and caches (`dashboard/*.json`) |
| `PROJECT-ag-delivery` | `core` | on | 7 days | 7 days, unlocked | objects go after 90 days, old versions after 30 | phone manifests and phone receipts |
| `PROJECT-ag-control` | `core` | off | none | — | — | the scan lock, `latest-run.json`, refresh requests and device status |
| `PROJECT-ag-display-current` | `core` | off | none | — | objects go after 2 days | `current.json`: the pre-drawn board frames |

The control and display buckets are `core` even for a household without a board,
because the scan job always writes to them.

Created by `provision-foundation.sh` (private state, delivery) and
`provision-dashboard.sh` (control, display). Incomplete uploads are cleaned up
after a day in the two versioned buckets.

Cloud Build uploads are restricted by `.gcloudignore` to application sources,
Docker/build inputs and the Cloud Build files. Inspect the configured build
staging location separately from runtime buckets.

```bash
gcloud storage buckets list --project=PROJECT \
  --format='table(name,location,versioning_enabled,soft_delete_policy.retentionDurationSeconds,retention_policy.retentionPeriod)'
gcloud storage buckets describe gs://PROJECT-ag-display-current --format='value(lifecycle_config)'
```

## Secrets

All are replicated only in `REGION` (user-managed replication).

| Secret | Module | Holds | Read by | Filled by |
| --- | --- | --- | --- | --- |
| `agskoleintra-school-username` | `core` | the parent's SkoleIntra login | `ag-sync` | the parent, `configure-runtime-secrets.sh` |
| `agskoleintra-school-password` | `core` | its password | `ag-sync` | the parent, `configure-runtime-secrets.sh` |
| `agskoleintra-openrouter-api-key` | `core` | the model key (ZDR route) | `ag-sync` | the parent, `configure-runtime-secrets.sh` (checks the key with OpenRouter first) |
| `agskoleintra-message-handle-key` | `core` | the key behind opaque message handles | `ag-sync` | `bootstrap-dashboard.py` from the Mac's Keychain when the local database is seeded; otherwise generated by `configure-runtime-secrets.sh` |
| `agskoleintra-device-token-hash` | `phone` | the hash of the iPhone's token | `ag-delivery` | `bootstrap-device-token.sh` |
| `agskoleintra-device-registry` | `phone` | each device's token hash and scopes | `ag-delivery` | `bootstrap-dashboard.py`, then `firmware/device.py setup` for the board |

```bash
gcloud secrets list --project=PROJECT \
  --format='table(name.basename(),replication.userManaged.replicas[0].location)'
gcloud secrets versions list SECRET --project=PROJECT --filter=state=ENABLED --format='value(name)' | wc -l
gcloud secrets get-iam-policy SECRET --project=PROJECT
```

## Images

Artifact Registry repository `agskoleintra` in `REGION`, Docker format,
immutable tags.

| Image | Dockerfile | Runtime extras | Used by |
| --- | --- | --- | --- |
| `app:main-SHA-DATE` | `Dockerfile` (Playwright) | browser, board, server, gcp | the scan job and the dashboard |
| `app:delivery-SHA-DATE` | `Dockerfile.delivery` (slim) | board, server, gcp | the delivery service |

```bash
gcloud run jobs describe agskoleintra-sync --project=PROJECT --region=REGION --format='value(spec.template.spec.template.spec.containers[0].image)'
```

## The scan job (`core`)

Cloud Run job `agskoleintra-sync`, deployed by `deploy-sync-job.sh`:

- runs as `ag-sync`, with the command `python -m skoleklar.cloud_job`;
- 1 CPU, 2 GiB, one task, no retries, 30-minute timeout;
- environment:
  - the four bucket names;
  - `AG_TIMEZONE`;
  - `AG_LLM_MODEL`;
  - `AG_SCHOOL_MESSAGE_MODE`, `AG_SCHOOL_MESSAGE_PROCESSOR` and
    `AG_MESSAGE_TOPICS`;
  - `AG_WEATHER_PLACE`, `AG_WEATHER_LATITUDE`, `AG_WEATHER_LONGITUDE` and
    `AG_WEATHER_MODE`;
- secrets as environment variables: `AG_SCHOOL_USERNAME`, `AG_SCHOOL_PASSWORD`,
  `OPENROUTER_API_KEY` and `AG_SCHOOL_MESSAGE_HANDLE_KEY`;
- invoked by `ag-scheduler`, `ag-delivery` and `ag-browser`, and by nobody else.

Choose message mode, processor and topics explicitly for the installation; do
not copy another project's values. The default message mode is `off`.

```bash
gcloud run jobs describe agskoleintra-sync --project=PROJECT --region=REGION
gcloud storage cat gs://PROJECT-ag-control/latest-run.json   # the last scan's summary; no school content
```

## Schedules (`core`)

Cloud Scheduler HTTP jobs in `REGION`, created from the configured scan times
by `create-schedules.sh`. The default three are listed below.
Each POSTs to the Cloud Run Admin API's `jobs/agskoleintra-sync:run`, with an
OAuth token for `ag-scheduler`. Their timezone comes from the school configuration; the default is
`Europe/Copenhagen`.

| Job | Schedule |
| --- | --- |
| `agskoleintra-morning` | `45 5 * * 1-5` |
| `agskoleintra-midday` | `30 12 * * *` |
| `agskoleintra-evening` | `30 19 * * *` |

Each scan pre-draws every board change for the next 36 hours, so the board
needs no triggers of its own. Verify their enabled/paused state explicitly.

```bash
gcloud scheduler jobs list --project=PROJECT --location=REGION --format='table(name.basename(),schedule,timeZone,state)'
```

## The delivery service and gateway (`phone`)

Cloud Run service `agskoleintra-delivery`, deployed by `deploy-delivery.sh`:

- runs as `ag-delivery` on port 8080;
- 1 CPU, 512 MiB, concurrency 20, 0 to 1 instances, 30-second timeout;
- private: no unauthenticated access, with the invoker check on;
- only `ag-gateway` may invoke it;
- environment: the delivery, display and control buckets, `AG_DEVICE_ID`, and
  `AG_REFRESH_JOB`;
- secrets: `AG_DEVICE_TOKEN_HASH` and `AG_DEVICE_REGISTRY`.

API Gateway, set up by `deploy-gateway.sh`:

- API `agskoleintra-delivery`, with a config rendered from
  `gateway-openapi.yaml` (backend auth as `ag-gateway`);
- gateway `agskoleintra-delivery` in `REGION`.

Its routes are:

- `/v1/probe`, `/v1/sync` and `/v1/receipts`;
- `/v1/refresh` and `/v1/refresh/{identifier}`;
- `/v1/display/status`, `/v1/display/current`, `/v1/display/frame`,
  `/v1/display/image`, `/v1/display/receipt` and `/v1/display/device-status`.

Every route needs an `x-api-key`, and the service then checks a device bearer.

The API key `agskoleintra-iphone-gateway-v2` may call only the gateway's
managed service. Its value lives only in the Mac's Keychain and on the
devices.

```bash
gcloud api-gateway gateways describe agskoleintra-delivery --project=PROJECT --location=REGION --format='value(apiConfig.basename(),state,defaultHostname)'
gcloud services api-keys list --project=PROJECT --format='value(displayName)'
```

## The browser board (`browser`)

Cloud Run service `agskoleintra-dashboard`, deployed by `deploy-browser.sh`
(`gcloud alpha`):

- runs as `ag-browser`, with the command
  `uvicorn skoleklar.browser_api:app`;
- IAP on, no unauthenticated access, invoker check on;
- 0 to 1 instances, 512 MiB, 30-second timeout;
- only the IAP service agent may invoke it;
- `roles/iap.httpsResourceAccessor` for each owner and viewer;
- environment: the display and control buckets, `AG_REFRESH_JOB`,
  `AG_IAP_AUDIENCE`, `AG_OWNERS`, `AG_VIEWERS`, `AG_BROWSER_ORIGIN` and
  `AG_BROWSER_ORIGINS`.

The service also checks the IAP token itself in `browser_api.py`.

```bash
gcloud run services describe agskoleintra-dashboard --project=PROJECT --region=REGION --format='value(metadata.annotations)'
```

## The e-ink board (`eink`)

No cloud resources of its own. `firmware/device.py setup` adds a display-only
entry to `agskoleintra-device-registry`. The board then calls the gateway with
the gateway key and its own token. The firmware accepts only a `*.gateway.dev`
host over HTTPS (see `firmware/firebeetle-cloud/cloud_app.cpp`).

## Alerts (`alerts`)

Built by `configure-monitoring.sh`:

- an email notification channel;
- the alert policy "AG delivery unusual backend request volume": more than 30
  requests reaching the delivery service in five minutes.

Check that the policy is enabled and the notification channel is verified.

```bash
gcloud monitoring policies list --project=PROJECT --format='value(displayName,enabled)'
```

## On the parent's Mac

The cloud does not hold everything. These stay local:

| Item | Where | Made by |
| --- | --- | --- |
| The iPhone's device token (plaintext) | Keychain, `ag-skoleintra` | `bootstrap-device-token.sh` |
| The gateway API key | Keychain, account `family-primary-iphone-gateway-api-key` | `deploy-gateway.sh` |
| The local controller's token | `.ag-school/devices/local-controller.json`, mode 0600 | `bootstrap-dashboard.py` |
| The message-handle key | Keychain, `ag-skoleintra` / `message-handle-key` | the first local scan with messages on, or `bootstrap-dashboard.py` when no message handle is stored |
| The verified local database and session | `~/.config/ag-skoleintra/` | local mode |

Copy a token to the phone with `uv run python -m skoleklar.device_token copy`
and `gateway-copy`, then clear the clipboard.
