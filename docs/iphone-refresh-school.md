# Refresh School on iPhone

For a builder connecting one primary iPhone to their own cloud deployment.
The Shortcut writes new/changed manifest items to shared Calendar and Reminders,
sends completions and receipts, and never deletes native items. Other family
phones receive the shared iCloud destinations; they do not run a second writer.

## Prerequisites and gateway selection

Complete the `phone` path in [GCP setup](../infra/gcp/SKILL.md). Confirm the
project's device registration and stored keys. The Shortcut is built for your
own gateway: `deploy-gateway.sh` prints its hostname, and gcloud shows it again:

```sh
gcloud api-gateway gateways describe agskoleintra-delivery --project=PROJECT_ID \
  --location=REGION --format='value(defaultHostname)'
```

On macOS, build the signed file with that hostname, without `https://` or a path:

```sh
uv run python -m skoleklar.phone_shortcut --host GATEWAY_HOST
```

The command refuses anything but a bare `*.gateway.dev` hostname. The file is
written to ignored `dist/Refresh School.shortcut`; `shortcuts sign --mode anyone`
supplies the signature. It uses the `family-primary-iphone` device identity and
the default native destination names. If changing those source-level constants,
also align the registered identity and manifest/Shortcut tests. The file carries
no keys: import questions collect them on the phone.

## What reaches the phone

The v2 manifest carries checked facts from today on. It leaves out ended
events, past due days, and past review items.

| Fact type | Destination |
| --- | --- |
| `event`, `schedule_change` | An all-day event in the `AG School` calendar |
| `bring` | A reminder in `School · Bring home`, alert at 07:00 on the due day |
| `homework`, `parent_action`, `reminder` | A reminder in `School · Deadlines`, alert at 07:00 on the due day |
| `substitute`, `context`, `routine` and other types | Not sent to the phone |

A fact eligible for phone delivery goes to the review list when it is flagged
for review, has confidence below 0.75, lacks a validated date, or needs a timed
calendar entry. The Shortcut applies the calendar and reminder lists. Maintainers
can inspect the review list and text brief in the manifest. [Delivery contracts](delivery.md#phone)

## Complete a reminder

Tick off the item in Apple Reminders under **School · Bring home** or
**School · Deadlines**. The next **Refresh School** run sends its stable key,
and the next scan records the completion. The board's checkboxes are display
markers. A maintainer on the configured writer can also record completion with
`uv run skoleklar item complete ITEM_ID`.

## Test the gateway from the Mac first

Before the phone, check the gateway with the harmless probe. Without the API key
it must answer 401 and reach nothing:

```sh
GATEWAY="https://GATEWAY_HOST"
curl -i -X POST "$GATEWAY/v1/probe" -H 'Content-Type: application/json' \
  --data '{"device_id":"family-primary-iphone","nonce":"no-key"}'
```

Then paste each value at a hidden prompt (copy them with `device_token
gateway-copy` and `device_token copy`) and send an authenticated probe:

```sh
read -r -s AG_GATEWAY_KEY
read -r -s AG_DEVICE_TOKEN
curl --fail-with-body -X POST "$GATEWAY/v1/probe" \
  -H "x-api-key: $AG_GATEWAY_KEY" -H "Authorization: Bearer $AG_DEVICE_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"device_id":"family-primary-iphone","nonce":"mac-test-1"}'
unset AG_GATEWAY_KEY AG_DEVICE_TOKEN
```

Expect JSON with `"ok": true` and the same device ID and nonce. A wrong token
gives 401 and an unknown route 404. Clear the clipboard afterwards.

## Install and check manually

1. Create an iCloud calendar `AG School` and reminder lists
   `School · Bring home` and `School · Deadlines`. Share them with the family.
2. AirDrop `dist/Refresh School.shortcut` to the primary iPhone and add it.
3. Answer the import questions. Copy the gateway key with
   `uv run python -m skoleklar.device_token gateway-copy` and the device token
   with `uv run python -m skoleklar.device_token copy`. Paste the token alone;
   the Shortcut supplies `Bearer`. Clear the clipboard after each transfer.
4. Inspect every request URL: it must name your project's gateway. Inspect the
   Calendar/Reminders actions and select the intended destinations if import
   did not resolve their names. Do not assume name matching worked.
5. Run manually and grant only the expected Calendar, Reminders, Files and
   network access. Check actual item titles, dates, notes and reminder times.
6. Run again with an unchanged manifest: it should not duplicate or rewrite items.
   Then use a controlled changed item to verify editing, and complete a test
   reminder to check its next-scan completion round trip.
7. Add phone automations only after those checks pass. Choose times after the
   configured cloud scans; for the default schedule, 06:15, 13:00 and 20:00 are
   possible choices, not settings this repository enables on the phone.

## Reconciliation behavior

Each native item's notes carry `[AGSKOLEINTRA:<stable_key>:r<revision>]`.
Keep that marker: it identifies an item across runs. The Shortcuts folder in
iCloud Drive holds `ag-school-state.json`, mapping keys to applied revisions.
A missing state file can recover from native markers; an interrupted run saves
progress after each successful item.

New items are created only when no marker match exists. Changed items are
edited in place; a completed reminder is not reopened. Two matches are reported
as `ambiguous_match` and left alone. Notifications report changes/failures rather
than claiming every requested write succeeded.

The phone sends completed stable keys to `/v1/sync`, then applied keys and
failures to `/v1/receipts`. The next school scan ingests them. An unchanged or
inapplicable manifest returns no new work. Expired/network-failed retrieval must
not produce native writes. The manifest omits past entries because the Shortcut
does not delete native records.

Stored review verdicts settle which facts become calendar/reminder entries;
unclear facts remain in the review list. Daily Note writing is not implemented,
and manifest brief text is not a localized finished Notes product.
See [delivery contracts](delivery.md) and [review checks](processing.md#review-checks).

## Acceptance limits

Structural and API tests cannot prove iOS import destination selection, edited
item behavior or alert timing. Those are installation acceptance checks above.
Keep phone screenshots, tokens, manifests and actual device results private.
