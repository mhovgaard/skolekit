# Composition rules

The state is an editorial projection of fresh structured facts, not a second
database. Keep fact selection in this common step; neither renderer may rewrite,
rank, or infer content.

## Time and coverage

- Use Europe/Copenhagen and the packet's `now`, not the model's assumed date.
- When the document includes `calendar`, copy its `week_label`, `hero.day` and
  the five `weekdays` date/day pairs exactly. These values are computed from the
  shared timing policy. Select facts for those dates; never move a fact to fit.
- On Monday through Thursday, use the current school day before 15:00 and the
  next school day from 15:00. On Friday before 15:00 use Friday. From Friday at
  15:00 through Sunday before 12:00, prepare the coming Monday as the state hero
  and its school week; the HTML renderer replaces the left school panel with the
  weekend activities already present in `weekend`. From Sunday at 12:00 show the
  coming Monday as “Næste skoledag”. Keep a still-pending urgent obligation
  visible when advancing. An explicit requested day/week takes precedence.
- Put up to two still-relevant Saturday/Sunday family or child activities in
  `weekend`. While the weekend cover is active, use only the current weekend,
  exactly the dates in `calendar.weekend`. The weekend closing Monday's school
  week is a different weekend and must be omitted from the current cover.
  Outside weekend mode, consider the current/upcoming weekend and the weekend
  closing the displayed school week. State rows contain only `day` and `text`;
  the calendar's ISO dates are guidance, not extra state fields.
  Never spend the hero on an empty weekend day, and never
  put a weekend activity in the footer.
- Use schema version 2. Represent the Monday-through-Friday school week containing
  the hero date as exactly five complete `weekdays` entries. Include ISO `date`, a
  short visible `day`, both child columns, an optional shared line, and a compact
  factual `summary` for every weekday. Do not pre-fold or omit a weekday: the shared
  renderer unfolds all five when they fit and folds the days farthest from the
  composition date only when content density requires it. On Friday this means
  Monday and then Tuesday fold first; on Monday, Friday and then Thursday fold
  first. Use a short coverage or no-recorded-items label for an empty date.
- Keep `footer` empty. The screen ends with one fixed weekend section; later
  weekday facts remain accounted for as omitted in the audit until their week.
- No known facts means “Ingen registrerede punkter”, not “Intet at huske”. Missing
  plans or failed source checks should be named, not interpreted as an empty day.
- Copy freshness from the packet, using composition time only for `composed_at`.
  `updated_label` reports source time. Freshness expires after 36 hours by default;
  both renderers warn on expired/partial state. Static output is a snapshot and
  needs rerendering to reflect later expiry.

## Selection and wording

1. Required things to bring, homework, deadlines, cancellations, and changed
   pickup/arrival times take priority.
2. Then show shared family events and meaningful school-day context.
3. Optional preparation must remain optional; conditional instructions must keep
   their condition. Never turn “may bring” into “must bring”.
   Any displayed `private_message_fact` must visibly say `Besked:` so the family
   can distinguish a message-derived instruction from homework, a calendar event,
   or an invitation. Weekend activity rows (point 6) are the exception. This is provenance from the sanitized fact, not a subject or
   body excerpt.
4. Collapse repeated periods under a short day/child/topic description. A broad
   school event may explain routine timetable noise, but cannot hide a distinct
   cancellation or required item. New topics need no configuration changes.
5. Merge a household event only when the facts actually identify the same event.
   Two generic private-event markers or identical dates do not establish identity.
6. A calendar event appearing under both children does not prove it applies to
   both classes. Use the event's stated audience; do not invent relevance.
   A calendar notice that a classmate `har fødselsdag` (`birthday_notice`) is
   only a classmate's birth date: not the child's event, an invitation, a
   message or evidence of family attendance. Always omit it. Only an explicit
   party or invitation can establish an actual birthday activity.
   `activity_kind` is a bounded category extracted from a private message:
   `birthday_party` (`Barn: Fødselsdagsfest`), `class_trip`
   (`Barn: Klassetur`), `class_event` (`Barn: Klassearrangement`) or
   `social_activity` (`Barn: Socialt arrangement`, arranged by parents). These
   are real planned activities the child attends, even when the message arrived
   weeks earlier: show those on the current weekend as events, with no
   `Besked:` prefix, ahead of routine notices, and keep the category word
   visible. One weekend row is one activity. Merge facts only when they report
   the same activity: the same child, date and `message_thread`, or one child's
   `class_trip` or `class_event` on one date. Activities from different message
   threads stay separate rows even when they share a category and date, such as
   two parties on one Saturday.
   When the current weekend has more activities than the two rows hold, fill
   both rows with activities, set `weekend_overflow` to the number of
   activities not shown, and audit their facts as `merged` at location
   `weekend_overflow`. Otherwise `weekend_overflow` is 0. No activity may
   disappear without being counted.
   A fact with `cancelled=true` is cancelled, and so is an activity with the
   same child, date and `message_thread` as a cancelled fact. Never show a
   cancelled activity as a plan: omit it, or show the change as
   `Barn: Fødselsdagsfest aflyst` when the family needs to know.
   `confirmed_activity_kind` is a bounded owner confirmation of an existing
   private event's child, date and activity category. Prefer this confirmed
   activity over routine notices: `class_trip` is `Barn: Klassetur`.
   A review flag may still concern an unknown time;
   display the confirmed category without inventing a time or place.
7. `needs_review` items remain uncertain. Resolve from already available,
   unambiguous structured evidence or omit the assertion with a reason in the
   audit. Surface a concise check only when it matters to a near-term decision.
8. Do not invent exact times for private facts that only retain a date. Keep
   privacy-safe bring-item labels; omit surrounding private discussion.
9. A substitute entry starts with its function and must name the substitute and
   affected lesson/subject and class: `Vikar: Alex. W.-U. · MAT 02`. Long names
   may use first name plus initials for every remaining name. Never display the
   scheduled teacher as a substitute.
   Merge repeated periods with the
   same child, date, substitute, subject and class into one line. Never reduce it
   to a generic “vikar” label. If a detail is unavailable, say which is missing.

Budget: hero two children × five lines; five weekdays × two children × three
lines plus one shared line and one compact summary per day; weekend two compact
rows plus the `weekend_overflow` count; messages one column per child. Weekend is a fixed lower-right area and
never consumes hero or weekday space.
Aim for roughly 18–22 characters per e-ink child line, then inspect the PNG.
When it is impossible to fit every important obligation, show a count such as
“+2 punkter i skolebrief” and retain all omitted references in the audit.

Child headings name the actual topics already grouped under the visible date. Use
labels such as `Idræt og dansk`, `Dansk`, `Frist`, `Fødselsdag` or
`Ingen punkter`. Do not repeat the weekday and do not use generic headings such
as `Husk`, `Til dagen` or `Dagens punkter`. Preserve the action in each line:
`Medbring: bevægeligt tøj`, `Læs s. 52–53; lav s. 26`, or `Skriv ugehistorie`.
When the hero date has more detail than its weekday row can hold, use the extra
hero lines. Put each unique substitute/subject/class group on its own hero line;
the weekday row may summarize overflow whose audited display location is hero.

## Message noticeboard

Copy the packet's checked state and aggregate unread count. Never substitute the
two visible rows for the total. Prefer unread/new threads, then recent activity.
Show only shortened participants, associated children, activity time, and allowed
flags. Use a date for older activity; a bare clock time could imply today.
Keep separate threads separate unless shared identity is established; matching
sender names alone are insufficient. A noticeboard is not a message preview.
Missing/old coverage must say messages were not freshly checked.

## Selection audit

Use `{ "source_updated_at": "…", "facts": [{"ref": "fact-1",
"decision": "displayed|merged|omitted", "location": "weekdays[0].children[0].lines[0]",
"reason": "…"}], "limitations": ["…"] }`.

Every packet fact appears exactly once. References stay in this local audit;
the device state contains no source identities. Explain omitted required facts,
ambiguities, and limited coverage so later revisions can be reviewed.
