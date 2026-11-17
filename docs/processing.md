# Facts, summaries and review

For maintainers changing deterministic processing or model behavior. The
versioned bundles in [manifests](../src/skoleklar/manifests/) and their strict
schemas define exact inputs/outputs. This guide describes stage responsibilities.

## Derivation

`pipeline.derive` selects meaningful changed published-page versions, removes
transport noise and deduplicates equivalent source input. Routine lesson rows
without meaning-bearing text are handled deterministically. Changed prose goes
to the model as compact normalized text and relevant fields, never source HTML,
login data or the database file.

The active `derive-v10` bundle splits prose into atomic obligations, changes,
events and context. Its fact types (`event`, `schedule_change`, `bring`,
`homework`, `parent_action` and others) are defined in its
[semantic layer](../src/skoleklar/manifests/derive-v10/semantic-layer.md); the
phone and board route by them. Each fact also says what it is, so nothing
downstream has to guess it from the wording: an event's or a meeting's `category`
(`trip`, `party`, `meeting`, `other`), whether a schedule change `moves_time`, and
whether an event is `recurring` (morning singing). A field on a type it does not
belong to is dropped, never sent for review. The fields live in the item's stored
payload; consolidation copies them from its members (a category when they agree,
a moved time when any says so), never from its model, and they are part of its
cache key. Facts derived before v10 have none, and the [parents'
board](parents-board.md#what-each-line-is) reads those as before.

The model reads each source once. A weekly plan goes as its days and general
blocks (subject and text), without its flattened `body_text`, the repeated
`content_data`, links, ids or code's heading guesses (`segments`): a week's plan
is about 12,000 characters instead of 45,000. `content_data` is left out for
every source whose `source_data` already holds it. Timing depends on meaning: homework under a weekday is due
before that school day, bring-items are needed on that day, explicit dates win,
and missing/uncertain dates are not guessed. Keep quantities, exceptions,
optionality and family participation attached to their facts.

Model output is checked against its schema, source evidence and timing rules.
Ambiguous audience, time or obligation stays flagged. Source links and member
identities are retained locally for traceability. Messages use a separate
restricted extractor, not `derive --source messages`; see
[message privacy](message-harvesting.md).

Model requests use the configured OpenRouter model with response storage off,
required parameters and ZDR provider routing. This still transmits school text
to that provider; ZDR is a retention boundary, not local-only processing.

## Versioning and evaluation

Each bundle names instructions, semantic definitions, few-shots and an output
schema. Its complete hash participates in stored audit and cache keys together
with input identity and model. Preserve existing bundle files so stored results
remain interpretable. Introduce a new bundle for a changed contract and update
the loader and hash/regression tests intentionally.

Use bounded validation backfills against a private copy, not the production
writer. They carry `purpose=validation`, including zero-item results, and stay
out of live briefs. An identical cached result may later be activated by normal
live processing. Dry-run output can contain private compact source text.

```sh
uv run skoleklar derive --db COPY.db --backfill-since YYYY-MM-DD --source weekly_plan --limit 25 --dry-run
uv run skoleklar derivation-report --db COPY.db --source weekly_plan --since YYYY-MM-DD
```

## Archival and consolidation

Archival is audited workflow state, not source deletion. Expired time-bound
facts are archived by policy; only ambiguous stale obligations need model
review. `item restore ITEM_ID` reverses archival through the supported workflow.
The scan archives before consolidation.

Consolidation considers the whole bounded set, refusing a partial partition
when it exceeds `--limit`. It distinguishes duplicates, complementary detail,
revisions and conflicts. An event, payment, RSVP and bring-item can be related
without being one completable item. Every input ID must occur exactly once.
Repair only removes unsafe proposed groups and keeps their members unclustered;
it never invents a merge or drops an input. Conflicts and unsafe inherited
fields stay for review. Input hashes and prompt versions make unchanged results
reusable.

```sh
uv run skoleklar archive --db COPY.db --as-of YYYY-MM-DD --dry-run
uv run skoleklar consolidate --db COPY.db --as-of YYYY-MM-DD --limit 100 --dry-run
uv run skoleklar archival-report --db COPY.db
uv run skoleklar consolidation-report --db COPY.db
```

## Day summaries

`day-summary-v2` retells a weekly plan as one child-friendly Danish paragraph
per school day. It keeps classroom activity separately from atomic obligations.
Input omits HTML, links and attachments. Where the plan names the child it is
for, code replaces the name with `[barnet]` (whole word, genitive `-s`, as the
profile writes it or in capitals: a name is often a word too, so `en bjørn i
skoven` and `1. maj` stay; a name a teacher writes in lower case stays unmarked),
so the child's own group, letters, turn or item reaches the summary as
`du`: `Du og Emma laver en bogstavhistorie med F og H`. The prompt permits staff
first names and the first names of classmates the plan puts with the child (their
group or pair, a classmate the class celebrates), and forbids naming other
children or adding sensitive matters; code cannot fully enforce the semantic
naming policy. `day-summary-v1` stays for the summaries it wrote.

Code validates day order, paired headline/text, length and relative-time rules,
and rejects `[barnet]` or the child's own name in the output.
Summary caching includes plan version, prompt and model. A changed plan makes
its previous summary unavailable until regenerated. The scan bounds the number
of plans and retries failures on a later run. State statuses are `ok`,
`no_content`, `not_published` and `unavailable`.

```sh
uv run skoleklar day-summaries --db COPY.db --dry-run
uv run skoleklar day-summaries --db COPY.db --show YYYY-MM-DD
```

The children's view and the parents' board consume summaries; they are not
another source of facts and do not alter the composition model's fact packet.
Frames carry the summaries of the composed week and of the parents' board's
school days ahead, which may reach into the next week.

## Review checks

After summaries, the scan revisits flagged facts from today through its review
window. Rules can reject another class's fact or a vague conference window
covered by an explicit child's appointment. Class context comes from stored
profile/typed data, not a name hardcoded in the prompt.

For remaining eligible facts with stored published-page evidence, the
`review-check-v1` model returns `show`, `drop` or `unclear`. Verdicts are keyed
by stable fact key and fingerprint; changed facts are checked again.
`show` clears the flag with a note, `drop` removes the fact from projections,
and `unclear` preserves uncertainty. Board and phone apply the same verdicts.
Message facts carry no stored evidence quotes and need a new message or
appropriate explicit confirmation rather than invented proof.

Review and summary failures do not silently turn uncertainty into a confirmed
fact. Keep fixed error classifications and safe counts in logs, not source
text or rejected model output. [Board generation](board-generation.md) performs
another independent set of projection checks.

## Brief and workflow

A brief's green/amber/red signal combines urgency, due-time and workflow state.
Untrusted timing cannot turn an ambiguous fact into a red required action.
Reading a brief does not mark it seen; only successful delivery should record
that it was surfaced. The CLI's `--record-delivery` is a write and belongs to
the configured writer. Completion/dismissal state follows stable keys across
re-derivation; phone receipt/completion ingestion happens at the next scan.
The writer can also record it by hand:

```sh
uv run skoleklar brief --from YYYY-MM-DD --to YYYY-MM-DD --compact
uv run skoleklar item acknowledge ITEM_ID     # also complete, dismiss or restore
```

`--compact` hides routine lessons and groups repeated timetable changes while
keeping every member's stable key.
