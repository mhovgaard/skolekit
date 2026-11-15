Every output fact is a minimal operational calendar fact. The durable projection
contains no source prose, subject, names, explanation, or evidence. `dedupe_hint`
is used only as transient stable-key material and is never stored. Dates use ISO
`YYYY-MM-DD`. `end_date` is null for a single-day event. `necessary_location` is
null unless the location is required to attend the event.

`all_day=false` means the private source contains an explicit clock time. The
date-level projection intentionally omits that time and must be routed to review,
where the live source message can be opened on demand.

`evidence_quote` is transient validation material. It must be copied exactly
from one contiguous source span and is never persisted.
