# Weekend event interpretation

For maintainers changing event identity, cancellations and weekend capacity.
Keep the known limits below visible when modifying projection rules.


The weather/weekend cover shows only the current weekend. The next school week's
closing weekend is a different weekend. On school-day views the fixed weekend
band can still include the closing weekend of the displayed school week.
Composition receives exact permitted weekend dates, and validation checks each
selected fact against its particular row's date.

A calendar notice that somebody `har fødselsdag` states a birth date. It does not
establish an invitation, party, or family attendance. The context tags these
notices and composition always omits them; the validator rejects any
composition that displays one. Source records, derivations, and birthday dates
remain intact.

Private-message event extraction (`message-events-v3`) assigns each event a
bounded `activity_kind`: `birthday_party`, `class_trip`, `class_event`,
`social_activity` (arranged by parents) or null. Dated invitations and
parent-arranged activities count as events even when the message also asks for
a reply. The board shows a categorized event on its weekend as an event, such
as `ChildA: Fødselsdagsfest`, without the `Besked:` prefix that other message
facts carry, and keeps the category visible. No message prose is retained. An owner may
confirm an existing event's activity category without retrieving or retaining
message prose. Cloud composition optionally reads the private object
`dashboard/event-confirmations.json` from `AG_PRIVATE_BUCKET`:

```json
{
  "schema_version": 1,
  "events": [{
    "stable_key": "message-event:<64 lowercase hex characters>",
    "child": "ChildA",
    "date": "2026-10-25",
    "kind": "class_trip"
  }]
}
```

Only `class_trip`, `class_event`, `birthday_party` and `social_activity` categories are accepted.
Unknown fields, free-text descriptions and invalid references are rejected. The
confirmation applies only while an existing private event matches its key,
child and date. It changes the dashboard projection, leaving source records,
phone delivery facts and time-review flags intact. Opaque keys stay out of the
composition packet and display. A confirmed class trip is shown as
`ChildA: Klassetur`; no time or place is inferred.

## Accounting for every weekend activity

The same rules apply to the HTML page and the e-ink frame, because both draw one
validated state. Code builds the weekend rows from the facts
(`dashboard_rules.weekend_selection`) instead of taking the model's: activities
first, preferring one with a bring-item from its own message thread when there
are more activities than rows; then cancelled activities as `aflyst`; then other
weekend facts. Each timeline slice rebuilds them and recalculates overflow.

- **Cancellations.** A fact stored as cancelled keeps `state: cancelled` through
  the brief. In the context it is `cancelled: true`, and an activity title gains
  `aflyst`. A cancelled activity, or one with the same child, date and message
  thread as a cancellation, must not be shown as a plan. The composition should
  omit it or show it as `ChildA: Fødselsdagsfest aflyst`. Enforcement
  currently covers categorized weekend activities; see the review limits below.
- **One row per activity.** Facts report the same activity when they share child,
  date and `message_thread`, a neutral per-export number that replaces the opaque
  thread handle. One child's `class_trip` or `class_event` on one date is always a
  single activity under the current heuristic, even across threads. Parties and
  parent-arranged activities from different threads stay separate rows, even
  when their category and date match. The class-event heuristic is a known
  limitation, not evidence that two events are identical.
- **No silent drops.** Every current weekend activity must be shown in a row,
  shown as cancelled, or counted in `weekend_overflow`. That count is drawn as
  `+1 aktivitet mere` under the plans on the cover and in the weekend band.
  Overflow is allowed only when both rows are in use.

`tests_python/test_dashboard_event_pipeline.py` starts from stored synthetic
message facts. It runs the real delivery items, brief and context, validates a
controlled composition and renders both outputs.

### Known limits

Cancellation enforcement currently covers categorized weekend activities;
weekday cancellations can still be displayed as active. The class-event
identity heuristic can group separate events for one child/date too broadly.
The narrow school-week e-ink band can also truncate a final cancellation marker:
`Fødselsdagsfest aflyst` fits without a prefix, but longer activity names may not.
These are limitations to test and fix, not proof that an initial validated
composition preserves every event meaning in every layout.

Weekend rollover selection is already rebuilt from facts. Keep the
Saturday-to-Sunday refill/overflow regression when changing that code, along
with distinct-event, cancellation and bring-item cases. Inspect rendered text,
not only whether `render_png` returns successfully.

Use a generation precondition when updating the private confirmation object.
For local preparation, pass the same file with `--event-confirmations PATH` to
the dashboard context exporter. Operational confirmations are private data and
must not be committed with real household event keys.
