Every output fact is a minimal operational fact. The durable projection contains
no source prose, subject, discussion, explanation, or evidence. `dedupe_hint` is
used only as transient stable-key material and is never stored. Dates use ISO
`YYYY-MM-DD`; `end_date` is null for a single-day fact. `necessary_location` is
null unless the location is required to attend an event.

Relative dates are anchored to `message_date`, not to the time when the inbox is
harvested or the dashboard is rendered. A message posted Thursday that says
`i morgen` therefore resolves to Friday even when processed on Friday or later.

A bring fact retains only bounded object labels, the child scope already supplied
by the caller, the date needed, and required-versus-recommended status. Object
labels are operational data even though the source message remains private. They
must not contain a person, condition, assessment, relationship, reason, or copied
sentence.

`all_day=false` means the private source contains an explicit clock time. The
date-level projection intentionally omits that time and must be routed to review,
where the complete live source message can be opened on demand without storage.

`evidence_quote` is transient validation material. It must be copied exactly from
one contiguous source span and is never persisted.
