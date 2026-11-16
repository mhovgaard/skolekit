---
name: ag-school-gcp
description: Build, check or update a household's Google Cloud setup for Skoleklar (scan job, buckets, secrets, phone gateway, browser board, schedules) with the gcloud scripts beside this file, in a safe order. Use when a household moves from local mode to GCP, to compare a live project with the target state, or to ship new images. Not Terraform; never deletes resources.
---

# Skoleklar on Google Cloud

There is no Terraform and no state file. Three things replace them:

- [target-state.md](target-state.md) says what a finished project contains. It
  gives each resource's settings and a read-only check for it.
- The scripts in this folder build it. Each one is idempotent and refuses to
  run unless the active gcloud project is the target.
- This file gives the order and the checks between steps.

Resolve sibling paths from this file's real location when it is reached through
a skill symlink. Run the scripts from the repository root.

## Rules

- **Check, show, then change.** Before a step, run its checks from
  [target-state.md](target-state.md) and show the parent what is missing. That
  list is the plan. Run a step only after the parent says yes. Each step changes
  their cloud project.
- **Never touch a secret value.** The parent runs any step marked *parent* in
  their own terminal, because it asks for a password, a key or a browser
  sign-in. Never pass a value as an argument, never echo or pipe it, and never
  read one with `gcloud secrets versions access`. Secret checks only count
  enabled versions.
- **One writer.** The cloud copy of the database becomes the truth when it is
  seeded (step 7). From then on, no local scan writes. Never seed while a scan
  is running.
- **Always pass the message mode** to `deploy-sync-job.sh`. The script defaults
  to `off`; choose the installation's mode, processor and topics explicitly.
- **Never** run `--replace`, `--rotate-openrouter`,
  `--rotate-message-handle-key`, or any delete, unless the parent asks for that
  exact action.
- **On a live project**, don't run `deploy-browser.sh` unless the owners and
  viewers in `skoleklar.toml` are the ones wanted: it replaces the service's
  lists with them. Don't run `deploy-gateway.sh` to add a route either: it also
  manages the API key. Use [Updating](#updating) instead.
- **The household's values live in its `skoleklar.toml`**, never in a command:
  the model, the scan times, the weather place, the owners and viewers. The
  scripts read them with `uv run skoleklar runtime show --json`; check that
  output before a deploy.

## Inputs

Collect these once, before step 1:

| Input | Example | Used by |
| --- | --- | --- |
| A dedicated project with billing | `ag-school-hansen` | every step |
| Region | `europe-west1` | every step |
| Modules | `core`, plus any of `phone`, `browser`, `eink`, `alerts` | which steps run |
| Owner's Google account | — | step 14 |
| Alert email | — | step 17 |
| Message mode, processor, topics | `off disabled off`, or `events-only zdr off\|model` | step 9 |
| `skoleklar.toml`: `[runtime]` (`where = "gcp"`, scan times, model), `[weather]` (place, latitude and longitude at postcode precision), `[gcp]` (project, region, owners, viewers) | — | steps 9, 14 and 16 read it |

The parent's Mac needs:

- `gcloud` with the alpha components;
- `uv`, `jq` and `sqlite3`;
- the repository with `uv sync --all-extras` done;
- a working Keychain (the scripts keep the device and gateway keys there).

## Sequence

Each step names who runs it, the module it belongs to, and the check that must
pass before the next step.

### Stage 0 · Local baseline (required today)

| # | Step | Run by | Check |
| --- | --- | --- | --- |
| 0.1 | `uv run skoleklar login`, then `uv run skoleklar check` | parent | the saved session works |
| 0.2 | With `[runtime] where = "local"`, one local scan in the cloud's message mode: `uv run skoleklar scan --message-mode events-only --message-processor zdr` (or `--message-mode off`) | parent | it ends `complete` |
| 0.3 | `uv run skoleklar db check --db ~/.config/skoleklar/school-restricted.db` | agent | every invariant passes |

This leaves these on the Mac:

- a verified database;
- the school session, in `auth-state.json`;
- with messages on, the message-handle key in the Keychain. With messages off
  there is no key yet; step 5 makes one.

The cloud starts from these. Starting the cloud without this local baseline
isn't supported; see [current limitations](#current-limitations-and-acceptance).

### Stage 1 · Foundation: nothing runs and nothing is reachable

| # | Step | Module | Run by | Check |
| --- | --- | --- | --- | --- |
| 1 | `gcloud auth login`, `gcloud auth application-default login`, `gcloud config set project PROJECT` | — | parent | `gcloud config get-value project` is the target |
| 2 | `./infra/gcp/provision-foundation.sh PROJECT REGION` | core | agent | the APIs, three identities, two buckets, five empty secrets and their grants are all in place |
| 3 | `gcloud secrets create agskoleintra-device-registry --project=PROJECT --replication-policy=user-managed --locations=REGION --labels=app=agskoleintra,environment=production` (labels it; step 5 would also create it in the region) | phone | agent | the secret exists, replicated in `REGION` |

### Stage 2 · Secrets and state, before any workload reads them

| # | Step | Module | Run by | Check |
| --- | --- | --- | --- | --- |
| 4 | `./infra/gcp/bootstrap-device-token.sh PROJECT` | phone | agent | `agskoleintra-device-token-hash` has 1 enabled version |
| 5 | `.venv/bin/python infra/gcp/bootstrap-dashboard.py PROJECT --region REGION` | core, phone | agent | the registry has 1 version. `agskoleintra-message-handle-key` holds the Keychain's key, and the script stops if a different key is already there. Without a Keychain key it makes one only when the local database holds no message handles, and refuses otherwise. |
| 6 | `./infra/gcp/configure-runtime-secrets.sh PROJECT` | core | parent | the school username, the password and the OpenRouter key each have 1 enabled version. The script leaves the handle key alone. |
| 7 | `./infra/gcp/seed-private-state.sh PROJECT` (it seeds `school-restricted.db`; the legacy `school.db` is never seeded) | core | agent | `gcloud storage ls -r gs://PROJECT-ag-private-state/state/` shows the database and `auth-state.json`. **From here the cloud copy is the truth:** set `[runtime] where = "gcp"` in `skoleklar.toml`, and every local writing command refuses. |

### Stage 3 · Images

| # | Step | Module | Run by | Check |
| --- | --- | --- | --- | --- |
| 8a | From a clean checkout of `main`: `./infra/gcp/build-image.sh PROJECT REGION main-SHA-DATE` | core | agent | the script prints the image URL |
| 8b | `gcloud builds submit . --project=PROJECT --config=infra/gcp/cloudbuild-delivery.yaml --substitutions=_IMAGE=REGION-docker.pkg.dev/PROJECT/agskoleintra/app:delivery-SHA-DATE` | phone | agent | the image is in the repository |

### Stage 4 · The writer: private, and run by hand

| # | Step | Module | Run by | Check |
| --- | --- | --- | --- | --- |
| 9 | `./infra/gcp/deploy-sync-job.sh PROJECT REGION APP_IMAGE MODE PROCESSOR TOPICS` (model, timezone and weather from `skoleklar.toml`) | core | agent | the job matches target-state.md. It refuses to deploy until all four core secrets have values. |
| 10 | `./infra/gcp/provision-dashboard.sh PROJECT REGION` (it may also run in stage 1; it grants on the job only once the job exists) | core | agent | the control and display buckets exist with their grants, along with the `agRefreshStatus` role. `ag-delivery` may invoke the job. |
| 11 | `gcloud run jobs execute agskoleintra-sync --project=PROJECT --region=REGION --wait` | core | agent | the execution succeeds and `latest-run.json` in the control bucket says it finished. `current.json` is in the display bucket. Read only event names in the logs, never their content. |

**Stop here if step 11 fails.** Nothing is reachable or scheduled yet, so a
failure costs nothing.

### Stage 5 · Readers, then the ways in

| # | Step | Module | Run by | Check |
| --- | --- | --- | --- | --- |
| 12 | `./infra/gcp/deploy-delivery.sh PROJECT REGION DELIVERY_IMAGE` | phone | agent | the service is private and nobody may invoke it yet |
| 13 | `./infra/gcp/deploy-gateway.sh PROJECT REGION` (takes up to ten minutes) | phone | agent | the gateway is `ACTIVE`, only `ag-gateway` may invoke the delivery service, and the key is in the Keychain |
| 14 | `./infra/gcp/deploy-browser.sh PROJECT REGION APP_IMAGE` (owners and viewers from `[gcp]`) | browser | agent | IAP is on, and the owner can open the printed URL and see the board. If IAP needs a console step, the parent does it there; never make the service public instead. |
| 15a | The iPhone Shortcut: `uv run python -m skoleklar.phone_shortcut --host GATEWAY_HOST` with the hostname step 13 printed, then `uv run python -m skoleklar.device_token copy` and `gateway-copy`, clearing the clipboard after each ([setup](../../docs/iphone-refresh-school.md)) | phone | parent | the Shortcut's probe succeeds |
| 15b | The board: `.venv/bin/python firmware/device.py setup --project PROJECT --region REGION`, then `provision` over USB ([bring-up](../../docs/board-build-guide.md)) | eink | parent | `firmware/device.py check` succeeds |

### Stage 6 · Turn it on

| # | Step | Module | Run by | Check |
| --- | --- | --- | --- | --- |
| 16 | `./infra/gcp/create-schedules.sh PROJECT REGION` (it lets `ag-scheduler` run the job, then one schedule per scan time in `skoleklar.toml`) | core | agent | a schedule per scan time is `ENABLED`, and `latest-run.json` changes after the next one runs |
| 17 | `./infra/gcp/configure-monitoring.sh PROJECT ALERT_EMAIL` | alerts | agent | the policy is enabled and the parent confirms the email channel |

## Why this order

- **What a step refers to exists before the step.**
  - Cloud Run checks a revision's secrets when it deploys.
  - `provision-dashboard.sh` grants a right on the job.
  - `deploy-browser.sh` grants a role that `provision-dashboard.sh` creates.
  - The gateway needs the delivery service's URL.
- **The handle key goes before anything that could make a new one.** The seeded
  database's message handles are made with the Mac's key. If
  `configure-runtime-secrets.sh` ran first, it would generate a different key,
  and every stored handle would stop matching.
- **The writer goes first on a first setup.** The first scan gives the readers
  something to serve. Updates go the other way: readers first, so they
  understand a new `current.json` before the job writes one.
- **Private before reachable.** Nothing faces the internet until a scan run by
  hand has passed its invariants.
- **Schedules last.** A schedule can't run a broken container unattended if it
  doesn't exist yet.

## Updating

This is the common case. It only swaps images, and it keeps every service's
environment, secrets, IAM and IAP settings as they are:

1. Build both images from a clean checkout of `main` (steps 8a and 8b).
2. Update the readers, then the job:

   ```bash
   gcloud run services update agskoleintra-delivery --project=PROJECT --region=REGION --image=DELIVERY_IMAGE
   gcloud run services update agskoleintra-dashboard --project=PROJECT --region=REGION --image=APP_IMAGE
   gcloud run jobs update agskoleintra-sync --project=PROJECT --region=REGION --image=APP_IMAGE
   ```

3. Check: each one reports the new image. Wait for the next scan, or run one by
   hand as in step 11.

A new gateway route needs a new API config. The steps are in
[README.md](README.md#updating-the-running-services).

## Back to local

1. Pause the three schedules with `gcloud scheduler jobs pause JOB
   --location=REGION`.
2. Copy the latest database and `auth-state.json` down into
   `~/.config/skoleklar/`. Ask before replacing local files.
3. Resume local scans.

Deleting the project or its resources is the owner's act, not this skill's.

## Current limitations and acceptance

A cloud start without a verified local baseline is unsupported: the cloud is
seeded from the local database and session. `bootstrap-dashboard.py` uploads
the handle key that matches that database: the Keychain's, or a new one when
the database holds no message handles. Do not generate another key around a
database that holds handles.

A successful update to an existing project is not proof that fresh-project
provisioning works. Run the full sequence in a new target and verify its
[resource specification](target-state.md), permissions, manual scan, outputs
and each selected client before enabling schedules. Keep project-specific
revision, secret-replication and acceptance records outside tracked docs.
