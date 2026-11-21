---
name: eval-ablation
description: Cut Skoleklar's model cost by replaying the scans' captured model calls on cheaper models and comparing each operation's answers with production's. Use to collect eval cases, run an ablation, score models per operation, or report which cheaper models would hold up. Evaluates and reports only; never changes the model, code or job.
---

# Eval ablation

The goal is to find the cheapest model for each operation that the scan's
code still accepts and that agrees with production about as often as
production agrees with itself.

## 1 · Collect

- Capture is on while the job has `SKOLEKLAR_EVAL_CAPTURE=1` ([processing.md](../../../docs/processing.md#eval-capture)).
  Each scan writes `dashboard/evals/DATE/RUN_ID.jsonl` to the private bucket.
  A lifecycle rule deletes the files after 30 days.
- Each file holds a run line, every prompt once, one line per call (input,
  output, usage, stage, seconds), then an outcome line (what reached the
  board).
- `message-*` operations are never captured.
- To turn capture off when the study ends:
  `gcloud run jobs update agskoleintra-sync --project=PROJECT --region=REGION --remove-env-vars=SKOLEKLAR_EVAL_CAPTURE`.
  A run of `deploy-sync-job.sh` also drops it.

## 2 · Rules

- Captures hold school text and the household's names. Copy them only into
  the session's scratch folder, show only scores, IDs and verdict labels,
  and delete the copies when the study is done.
- Replays go through OpenRouter with ZDR, the same as the scan. The key is
  read in-process with `openrouter_api_key()` and never printed.
- Say what the replay will cost before running it: about the number of
  calls × models × production's cost per call × the price ratio.
- Check the account's balance first (`GET https://openrouter.ai/api/v1/credits`
  with the key, read in-process). Replays and the production scans share
  one balance. Don't run a replay that would leave less than a few days of
  scans (about $1 a day). OpenRouter holds back each in-flight request's
  maximum cost, so with a low balance parallel calls fail with 402 even
  when the replay's real cost would fit.
- Count only calls that returned an answer. A model that errors out
  (402, `missing_output_text`, `max_output_tokens`) hasn't been measured on
  that call. Report it separately; don't count it as a miss.
- The scan's output budgets (`max_output_tokens` per operation) were set for
  a model that reasons little. Reasoning models spend that budget thinking,
  and then fail with `max_output_tokens` or `missing_output_text`. Run them
  in two variants: as production calls them, which measures them as they
  would run today, and with reasoning limited or a larger budget, which
  measures quality. Count the extra tokens in the cost.
- Never change the job's model or environment, or the code; only report.

## 3 · Replay and score

```bash
gcloud storage cp -r gs://PROJECT-ag-private-state/dashboard/evals/ SCRATCH/captures/
uv run python -I .claude/skills/eval-ablation/scripts/replay.py SCRATCH/study SCRATCH/captures/*/*.jsonl -- PRODUCTION_MODEL CANDIDATE...
uv run python -I .claude/skills/eval-ablation/scripts/replay.py SCRATCH/study SCRATCH/captures/*/*.jsonl -- report
```

- Always include the production model. Its rerun is the noise floor.
- Each replay adds to the study folder, and rerunning a model replaces its
  rows. So models can be run in batches and reported together.
- `report` prints, per model and operation: calls answered out of calls
  made, errors by code, mean agreement with its range, cost and its share of
  production's captured cost, and seconds per call. Each model gets an `all`
  row. Then it prints the review-check rates with their intervals. Show this
  table in the report.
- Use `rescore` after changing a metric. It makes no new model calls.
- Get prices from `https://openrouter.ai/api/v1/models`. Compare total cost
  per operation, not the list price per token: input is most of the cost
  here, and models differ in how many output and reasoning tokens they use.

| Operation | Accepted when | Agreement with production |
| --- | --- | --- |
| derive | always (`validate_items` adds flags) | F1 on (type, date, child), plus the number of items flagged |
| consolidate | after `repair_consolidation_output` | F1 on the pairs of items grouped together |
| review-check | always | the rate of verdicts that differ, per distinct fact, split into stricter and looser |
| day-summary | `validate_output` passes | F1 on facts (the prose isn't judged) |
| dashboard | `build_state` passes | F1 on the facts referenced, plus F1 on the facts omitted |

## 4 · Decide, per operation

- Every answer must pass the code's checks. A rejected answer fails the
  operation for that model.
- Agreement has to be within the production rerun's spread, judged with its
  95% Wilson interval. Five cases only rule out large gaps. Roughly 60–90
  distinct cases pin a rate to about ±10%.
- Production is a reference, not the truth. Read the disagreements: being
  stricter costs an extra unsure mark, and being looser can show a doubtful
  fact as certain. Looser counts for more.
- If production only ever says "show", the looser direction can't be
  measured. Get cases where production holds facts back before trusting a
  cheap model with review-check.
- Derive and consolidate disagree with themselves on a rerun (production
  rerun agreed 0–100% on derive and 39–100% on consolidate). Score them on
  the facts that reach the board (the outcome line), and use several
  reference samples, before ranking models on them.

## 5 · Report

This skill evaluates and analyses only. It never changes the model, the
code or the job; that is a separate decision for the parent.

Assume one model serves every operation, as it does today (`AG_LLM_MODEL`).
Report:

- the cheapest model that passes all operations, and its cost as a share of
  production's;
- for each operation, the cheapest model that passes it, with its
  agreement, its interval and the number of cases;
- what a split would add over the single model. Derive and consolidate are
  about 70% of the cost, so a split only matters when they need a mid or
  large model and the other operations pass on a small one. In the first
  replay a split added 12–26% of production's cost in savings, against
  about 2% when a small model passed derive and consolidate too;
- what the data can't show yet (too few cases, a direction it can't
  measure, noisy operations).
