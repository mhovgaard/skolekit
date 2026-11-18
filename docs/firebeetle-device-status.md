# Board status and troubleshooting

For builders operating the board and maintainers changing diagnostics. The
board's local HTTPS page is `https://firebeetle-ag.local/`; it requires its
private admin password and trusted per-device certificate. See
[setup](board-build-guide.md#7-open-its-own-page) before using it.

## What the status means

The main rows distinguish uptime, Wi-Fi, authenticated cloud contact, the last
sync, saved-frame readiness and the physical display. `OK`, `WAIT` and `FAIL`
include text as well as colour. An old cached image is not described as a new
successful draw, and expired data is not healthy merely because it is visible.

| Action | Does |
| --- | --- |
| Sync now / USB `sync` | Fetch, verify, save, read back and draw the current published frame |
| Refresh status / USB `status` | Read local device state |
| Saved-frame check | Read and verify flash without contacting the cloud |
| School-data refresh in the browser board | Request a new school scan when authorized; separate from a board sync |

In sleep mode the board's own page is available only while awake. Press the
GPIO27 user button to keep it awake for maintenance. Device modes never grant
permission to change the school's site.

After a sync the firmware posts its latest device report to the control store.
The authenticated browser board's **Tavlestatus** page, `/board-status`, shows
that report with battery readings, planned wake and overdue/asleep state. It
is a report from a past instant, not a live connection while the board sleeps.
Use local HTTPS or USB for device controls.

The top of Tavlestatus shows what the last scan spent on the model: US dollars
as OpenRouter reports them, tokens in, out and cached, model calls and the
split by stage. Below it, a timeline draws cost and tokens per run for the last
30 days (hover a bar for that run). Every model call goes through
`OpenRouterClient.generate`, which adds its usage to the run's meter
(`skoleklar.processing_cost`); each scan, failed ones included, appends its
totals to `processing-cost.json` in the control store (newest 120 runs) and to
`latest-run.json` as `processing`. Only counts and money are stored.

## Measurements

Last-sync status includes bytes received, application read chunks, speed,
network download time, flash read-back and total operation duration. These
are different measurements: a chunk is not a TCP packet and a local read-back
has zero downloaded bytes. Persistent sync counters are separate from cache
checks; reboot or a failed later attempt must not overwrite a previous
successful result as though it just happened.

```sh
.venv/bin/python firmware/device.py status
.venv/bin/python firmware/device.py status --details
.venv/bin/python firmware/device.py sync
.venv/bin/python firmware/device.py watch --minutes 20
```

Only one serial program can hold the port. Close monitors before uploading.
A timer wake should finish its sync/report and sleep; a maintenance wake can
stay awake longer. Timer-drift samples describe learned scheduling correction,
not battery calibration. See [power policy](../firmware/README.md#power-and-wake-policy).

## When something is wrong

- Missing USB port: check the CH340 driver and a data-capable cable.
- Busy port: close another monitor/watch process.
- No Wi-Fi: check the 2.4 GHz network, then the provisioned SSID.
- Older board displayed: inspect the failed sync, publication expiry and the
  cloud's latest run outcome; preserve the last good frame while diagnosing.
- Overdue device: distinguish an expected long/low-battery sleep from a missed
  wake or failed status report. A failed report does not prove the draw failed.
- Implausible battery voltage: calibrate on the cell; USB charging can change
  the reading. Measure current before estimating endurance.

Native/API tests cover accounting and failure states. New hardware or firmware
still needs device acceptance for authentication, sync/draw, reboot recovery,
sleep/wake and status-report freshness. Keep that evidence outside public docs.
