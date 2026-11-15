# Live subjects on the household display

For maintainers and agents rendering an explicitly opted-in subject overlay.
Only the latest subject per child may pass the render-time sensitivity check.
The exception covers the final current HTML/PNG, not the database, saved state
or historical screenshots. It is not enabled by a previous household's choice.

The [parents' board](../../../docs/parents-board.md) can show a shortened sender,
a short topic and date. Model topics are a separate opt-in:
`AG_MESSAGE_TOPICS=model` (default `off`; the deployment helper requires the
`zdr` processor). See [Model topics](#model-topics).

Run on the authenticated Mac using the project's Python dependencies:

```bash
uv run --extra board python rendering/ag-school-eink-renderer/scripts/render_dashboard.py \
  rendering/private/dashboard-state.json --live-subjects --format both \
  --output-dir rendering/private/current
```

HTML alone:

```bash
.venv/bin/python rendering/ag-school-html-renderer/scripts/render_html.py \
  rendering/private/dashboard-state.json --live-subjects \
  --output-dir rendering/private/current
```

Both outputs share one live fetch per child when rendered in one invocation.
Always overwrite these current outputs. Do not produce dated copies, subject
JSON files, screenshots, or test fixtures from real messages. The ESP32 consumes the finished packed frame without school credentials.

## Read and disclosure boundary

- Read the first conversation in the server's latest-first inbox ordering, then
  request its exact `LatestMessageId`; do not assume the thread title or participant
  list identifies the latest subject/sender. Outgoing messages are included if
  they are actually latest, matching `message get --child --latest` semantics.
- Use only GET endpoints. Do not update read status or the CLI retrieval marker.
  Expired authentication produces “Emne utilgængeligt”; there is no old-subject
  cache or implicit login/harvest.
- The school's response can include body/history fields; do not inspect, normalize,
  log or persist them. Only subject, shortened sender, activity and unread status
  reach the in-memory display check.
- `rendering/shared/live_subjects.py` applies the documented disclosure policy to
  the entire subject before truncation. Ordinary school wording is readable by
  default, including activities, birthdays, invitations, staff surprises and
  harmless subjects containing a name. Broad class/group messages are readable
  even when they use a normally sensitive topic word. Redaction is reserved for
  wellbeing, relations between children (including bullying), mental health and
  diagnoses, private academic/behavioural matters, family matters, financial
  matters, and individually named issues without harmless event context. General
  illness (lice, flu) and staff changes are readable under the implemented policy. Normal punctuation and emoji are accepted;
  malformed control text, markup, contact details and identity-like digit
  sequences fail closed. Expand or
  narrow these categories only with reviewed synthetic examples.
- No subject is written to SQLite, context/state/audit JSON or logs, and none is
  sent to a model unless the household opted in to model topics. A passing fragment exists in the current HTML/PNG and physical display.
  No full subject is hidden in HTML attributes, tooltips, comments or accessibility
  labels. The HTML contains only the approved shortened fragment; CSS may shorten
  it further. PNG truncation uses pixel width.
- A sensitive result carries no subject text, only sender plus `(redacted)`.
  Retrieval failure is distinct from redaction and shown per child.
- School-data freshness remains independent from the live subject fetch. The live
  noticeboard does not present the snapshot's unread total as a fresh total.

The saved v1 state allowlist continues to reject every subject field. The overlay
is a separate function argument and is never merged back into the state object.

## Model topics

With `AG_MESSAGE_TOPICS=model` the cloud sync sends each subject that passed the
local check, and nothing else (no sender, recipients or body), in one request
through the zero-data-retention route (`message_topics.py`, prompt
`message-topic-v1`). The model returns a topic of one or two Danish words, or hides
it for the categories above. Code accepts a topic only when it has one or two
words, at most 24 characters, no digits and no child's name; otherwise the line
keeps sender and date. A hidden topic marks the message redacted. A failed request
leaves every line with sender and date. Topics exist only in the rendered frames.
