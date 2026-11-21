"""Replay captured eval calls on other models and score each against production's answer.

From the repository root, with `uv run python -I`:
  replay.py OUT_DIR CAPTURE.jsonl... -- MODEL...   replay every captured call on each model
  replay.py OUT_DIR CAPTURE.jsonl... -- rescore    re-score the saved answers; no model calls
  replay.py OUT_DIR CAPTURE.jsonl... -- report     the per-operation table and review-check rates
Replays add to OUT_DIR/results.json (scores only; a rerun replaces the same model's rows).
OUT_DIR/outputs/ holds the models' answers: private, so keep OUT_DIR in a scratch folder.
REPLAY_WORKERS sets how many calls run at once (default 3).
"""
import json
import os
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from skoleklar.triage.derivation import validate_items
from skoleklar.triage.prompt_manifest import load_prompt_manifest


class Captured:
    """The manifest as sent: the captured prompt with the repository's schema."""

    def __init__(self, manifest_id, text):
        base = load_prompt_manifest(manifest_id)
        self.manifest_id, self.text = manifest_id, text
        self.response_schema_name, self.output_schema = base.response_schema_name, base.output_schema
        self.content_hash = base.content_hash

    def render_instructions(self):
        return self.text


def operation(manifest_id):
    return manifest_id.rsplit("-v", 1)[0]


def f1(ref, cand):
    ref, cand = Counter(ref), Counter(cand)
    if not ref and not cand:
        return 1.0
    hit = sum((ref & cand).values())
    p, r = hit / max(sum(cand.values()), 1), hit / max(sum(ref.values()), 1)
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def refs(node, found=None):
    """Every fact reference in a dashboard answer."""
    found = set() if found is None else found
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("ref", "refs", "fact_refs") and isinstance(value, str):
                found.add(value)
            elif key in ("refs", "fact_refs") and isinstance(value, list):
                found |= {v for v in value if isinstance(v, str)}
            else:
                refs(value, found)
    elif isinstance(node, list):
        for value in node:
            refs(value, found)
    return found


def accepted_by(check):
    try:
        check()
        return True
    except Exception as error:  # noqa: BLE001
        return f"{type(error).__name__}: {str(error)[:80]}"


def score(call, output):
    """Agreement with production's answer (1.0 = the same), and whether the scan's code accepts it."""
    op, ref, doc = operation(call["manifest_id"]), call["output"], call["input"]
    if op == "derive":
        key = lambda i: (i["type"], i["effective_date"], i["child"])
        candidate = {"source_type": doc.get("source_type"), "source_key": doc.get("source_key"),
                     "sources": [{"child": doc.get("child"), "version_id": v}
                                 for v in doc.get("source_version_ids") or [None]]}
        flagged = lambda items: sum(i["needs_review"] for i in validate_items(
            json.loads(json.dumps(items)), candidate=candidate, document=doc))
        return {"agree": f1(map(key, ref["items"]), map(key, output["items"])), "accepted": True,
                "flagged": flagged(output["items"]), "ref_flagged": flagged(ref["items"])}
    if op == "consolidate":
        from skoleklar.triage.consolidation import repair_consolidation_output
        ids = [{"id": item["item_id"]} for item in doc["items"]]
        pairs = lambda out: [(a, b) for g in repair_consolidation_output(out, items=ids)[0]["groups"]
                             for a in g["member_item_ids"] for b in g["member_item_ids"] if a < b]
        return {"agree": f1(pairs(ref), pairs(output)), "accepted": True}
    if op == "review-check":
        ref_v = {v["id"]: v["verdict"] for v in ref["verdicts"]}
        got = {v["id"]: v["verdict"] for v in output["verdicts"]}
        return {"agree": sum(got.get(i) == v for i, v in ref_v.items()) / max(len(ref_v), 1), "accepted": True}
    if op == "day-summary":
        from skoleklar.day_summaries import validate_output
        facts = lambda day: [json.dumps(f, sort_keys=True) for f in day.get("facts") or []]
        per_day = [f1(facts(r), facts(c)) for r, c in zip(ref["days"], output["days"])]
        return {"agree": sum(per_day) / max(len(per_day), 1),
                "accepted": accepted_by(lambda: validate_output(doc, output))}
    if op == "dashboard":
        from skoleklar.dashboard_build import build_state
        return {"agree": f1(refs(ref), refs(output)), "accepted": accepted_by(lambda: build_state(doc["context"], output)),
                "omitted_agree": f1(refs(ref.get("omitted")), refs(output.get("omitted")))}
    return {"agree": None, "accepted": None}


def load(captures):
    calls, prompts = {}, {}
    for path in captures:
        run = None
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["kind"] == "run":
                run = record["run_id"]
            elif record["kind"] == "prompt":
                prompts[record["sha256"]] = record["text"]
            elif record["kind"] == "call" and record.get("output") is not None:
                calls[(run, record["seq"])] = {**record, "run": run, "prompt": prompts[record["prompt_sha256"]]}
    return calls


def answer_path(out_dir, row):
    return out_dir / "outputs" / f"{row['model'].replace('/', '_')}-{row['run']}-{row['seq']}.json"


def replay(out_dir, calls, models):
    from skoleklar.settings import openrouter_api_key
    from skoleklar.triage.derivation import OpenRouterClient
    key = openrouter_api_key()

    def one(job):
        model, call = job
        row = {"model": model, "run": call["run"], "seq": call["seq"], "manifest_id": call["manifest_id"]}
        started = time.monotonic()
        try:
            response = OpenRouterClient(api_key=key, model=model, timeout=240).generate(
                call["input"], manifest=Captured(call["manifest_id"], call["prompt"]),
                max_output_tokens=call["max_output_tokens"])
        except Exception as error:  # noqa: BLE001
            return {**row, "seconds": round(time.monotonic() - started, 1),
                    "error": getattr(error, "error_code", None) or f"{type(error).__name__}: {str(error)[:120]}",
                    "finish": getattr(error, "finish_reason", None),
                    "cost": (getattr(error, "usage", None) or {}).get("cost")}  # billed even when unusable
        answer_path(out_dir, row).write_text(json.dumps(response["output"], ensure_ascii=False))
        row.update(seconds=round(time.monotonic() - started, 1), finish=response.get("finish_reason"),
                   cost=(response.get("usage") or {}).get("cost"))
        try:
            row.update(score(call, response["output"]))
        except Exception as error:  # noqa: BLE001
            row["score_error"] = f"{type(error).__name__}: {str(error)[:120]}"
        return row

    with ThreadPoolExecutor(int(os.environ.get("REPLAY_WORKERS", "3"))) as pool:
        return list(pool.map(one, [(model, call) for model in models for call in calls.values()]))


def rescore(out_dir, calls, rows):
    for row in rows:
        if "error" in row:
            continue
        row.pop("score_error", None)
        try:
            row.update(score(calls[(row["run"], row["seq"])], json.loads(answer_path(out_dir, row).read_text())))
        except Exception as error:  # noqa: BLE001
            row["score_error"] = f"{type(error).__name__}: {str(error)[:120]}"
    return rows


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 1.0
    p, d = k / n, 1 + z * z / n
    centre, half = (p + z * z / (2 * n)) / d, z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / d
    return max(0.0, centre - half), min(1.0, centre + half)


def report(out_dir, calls, rows):
    """Per model and operation: answered, errors, agreement, cost as a share of production's captured cost."""
    prod_cost = defaultdict(float)
    for call in calls.values():
        prod_cost[operation(call["manifest_id"])] += (call.get("usage") or {}).get("cost") or 0
    cells = defaultdict(lambda: {"n": 0, "ok": 0, "agree": [], "cost": 0.0, "seconds": 0.0, "errors": Counter()})
    for row in rows:
        cell = cells[(row["model"], operation(row["manifest_id"]))]
        cell["n"] += 1
        cell["cost"] += row.get("cost") or 0
        cell["seconds"] += row.get("seconds") or 0
        problem = row.get("error") or row.get("score_error") or (row.get("accepted") if row.get("accepted") is not True else None)
        if problem:
            cell["errors"][problem] += 1
        else:
            cell["ok"] += 1
            cell["agree"].append(row["agree"])
    ops = sorted(prod_cost)
    print(f"{'model':30} {'operation':13} {'ok/n':>5} {'agree [min–max]':>16} {'cost':>7} {'share':>6} {'s/call':>6}  errors")
    for model in sorted({m for m, _ in cells}):
        total, ok, n = 0.0, 0, 0
        for op in ops:
            c = cells.get((model, op))
            if not c:
                continue
            total, ok, n = total + c["cost"], ok + c["ok"], n + c["n"]
            ag = c["agree"]
            agree = f"{sum(ag) / len(ag):.2f} [{min(ag):.2f}–{max(ag):.2f}]" if ag else "–"
            errors = ", ".join(f"{e} ×{k}" for e, k in c["errors"].most_common())
            print(f"{model:30} {op:13} {c['ok']:>2}/{c['n']:<2} {agree:>16} {c['cost']:7.4f} "
                  f"{c['cost'] / (prod_cost[op] or 1):6.0%} {c['seconds'] / c['n']:6.1f}  {errors}")
        print(f"{model:30} {'all':13} {ok:>2}/{n:<2} {'':>16} {total:7.4f} {total / (sum(prod_cost.values()) or 1):6.0%}")
    print("\nreview-check, per distinct fact (stricter: production showed it, the model didn't; looser: the reverse)")
    print(f"{'model':30} {'facts':>5} {'differ':>6} {'rate':>5} {'95% CI':>9} {'stricter':>8} {'looser':>6}")
    facts = defaultdict(dict)
    for row in rows:
        if operation(row["manifest_id"]) != "review-check" or "error" in row:
            continue
        call = calls[(row["run"], row["seq"])]
        items = {i["id"]: i for i in call["input"]["items"]}
        got = {v["id"]: v["verdict"] for v in json.loads(answer_path(out_dir, row).read_text())["verdicts"]}
        for v in call["output"]["verdicts"]:
            i = items[v["id"]]
            facts[row["model"]][(i.get("child"), i.get("type"), i.get("date"), i.get("title"))] = (v["verdict"], got.get(v["id"]))
    for model, pairs in sorted(facts.items()):
        n = len(pairs)
        stricter = sum(r == "show" and g != "show" for r, g in pairs.values())
        looser = sum(r != "show" and g == "show" for r, g in pairs.values())
        differ = sum(r != g for r, g in pairs.values())
        low, high = wilson(differ, n)
        print(f"{model:30} {n:5} {differ:6} {differ / n:5.0%} {f'{low:.0%}–{high:.0%}':>9} {stricter:8} {looser:6}")


def main():
    out_dir, split = Path(sys.argv[1]), sys.argv.index("--")
    calls, args = load(sys.argv[2:split]), sys.argv[split + 1:]
    results = out_dir / "results.json"
    rows = json.loads(results.read_text()) if results.exists() else []
    if args == ["report"]:
        return report(out_dir, calls, rows)
    if args == ["rescore"]:
        rows = rescore(out_dir, calls, rows)
    else:
        (out_dir / "outputs").mkdir(parents=True, exist_ok=True, mode=0o700)
        rows = [r for r in rows if r["model"] not in args] + replay(out_dir, calls, args)
    results.write_text(json.dumps(rows, indent=1))
    print(f"{len(rows)} rows in {results}")


if __name__ == "__main__":
    main()
