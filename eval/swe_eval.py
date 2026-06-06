#!/usr/bin/env python3
"""
SWE-bench-style batch evaluator.

For each case in swe_dataset.json:
  1. check out the repo at the PRE-FIX commit (base_commit),
  2. run the agent (implement mode) on the issue,
  3. compare the agent's diff against the gold PR diff.

Metrics (per case + aggregate):
  - localized:  did we edit at least one of the files the real PR changed?
  - file_recall / file_precision over Go SOURCE files (tests excluded),
  - exact_file_set:  did we touch exactly the gold Go files?
  - produced_fix:    did we emit a non-empty diff at all?
  - diff sizes (ours vs gold).

For throughput, the batch defaults to 1 candidate and skips Go tests (the comparison
is diff-vs-gold, not validation). Override with GIA_NUM_CANDIDATES / GIA_SKIP_TESTS.
Resumable: cases with an existing eval.json are skipped.

Usage:
    python -m eval.swe_eval --provider azure
    python -m eval.swe_eval --provider azure --limit 5
"""

import argparse
import json
import os
import re
from pathlib import Path

# Batch defaults (set BEFORE importing the pipeline, which reads them at import).
os.environ.setdefault("GIA_NUM_CANDIDATES", "1")
os.environ.setdefault("GIA_SKIP_TESTS", "1")

ROOT = Path(__file__).parent.parent
RUNS = ROOT / "eval" / "runs"

# Load .env (provider keys, Azure endpoint/deployment) like solve.py does.
_env = ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.split("#")[0].strip())


def _our_changed_files(diff: str) -> list[str]:
    return re.findall(r"^diff --git a/(.+?) b/", diff, re.MULTILINE)


def _go_src(files) -> set:
    return {f for f in files if f.endswith(".go") and not f.endswith("_test.go")}


def evaluate_case(case: dict, provider: str, model: str) -> dict:
    from agent.github_client import Issue
    from agent.pipeline import run_issue

    repo = case["repo"]
    n = case["issue_number"]
    issue = Issue(number=n, title=case["issue_title"], body=case.get("issue_body", ""),
                  labels=[], url=f"https://github.com/{repo}/issues/{n}", repo=repo)

    run_issue(issue, clone_dir="./workspace", output_dir=str(RUNS),
              provider=provider, model=model, base_commit=case["base_commit"])

    our_patch = RUNS / f"issue-{n}" / "changes.patch"
    our_diff = our_patch.read_text() if our_patch.exists() else ""
    our_files = _our_changed_files(our_diff)
    our_src, gold_src = _go_src(our_files), _go_src(case["gold_src_files"])
    hit = our_src & gold_src

    return {
        "issue_number": n,
        "pr_number": case.get("pr_number"),
        "subject": case.get("subject", ""),
        "gold_src_files": sorted(gold_src),
        "our_src_files": sorted(our_src),
        "localized": bool(hit),
        "file_recall": round(len(hit) / len(gold_src), 3) if gold_src else 0.0,
        "file_precision": round(len(hit) / len(our_src), 3) if our_src else 0.0,
        "exact_file_set": our_src == gold_src and bool(gold_src),
        "produced_fix": bool(our_diff.strip()),
        "our_diff_bytes": len(our_diff),
        "gold_diff_bytes": len(case.get("gold_diff", "")),
        "status": "ok",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="azure")
    ap.add_argument("--model", default=None)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    # Resolve model the same way solve.py does (Azure → deployment name).
    model = args.model
    if args.provider == "azure":
        model = model or os.environ.get("AZURE_OPENAI_DEPLOYMENT") or "gpt-4o"

    dataset = json.loads((ROOT / "eval" / "swe_dataset.json").read_text())
    if args.limit:
        dataset = dataset[: args.limit]
    RUNS.mkdir(parents=True, exist_ok=True)

    print(f"SWE eval: {len(dataset)} cases | provider={args.provider} model={model} "
          f"| candidates={os.environ['GIA_NUM_CANDIDATES']} skip_tests={os.environ['GIA_SKIP_TESTS']}\n")

    results = []
    for i, case in enumerate(dataset, 1):
        n = case["issue_number"]
        eval_path = RUNS / f"issue-{n}" / "eval.json"
        if eval_path.exists():                       # resume
            results.append(json.loads(eval_path.read_text()))
            print(f"[{i}/{len(dataset)}] #{n}: cached")
            continue
        print(f"\n{'#'*70}\n[{i}/{len(dataset)}] Evaluating issue #{n} at base {case['base_commit'][:10]}\n{'#'*70}")
        try:
            res = evaluate_case(case, args.provider, model)
        except Exception as e:
            import traceback; traceback.print_exc()
            res = {"issue_number": n, "status": f"error: {type(e).__name__}: {e}",
                   "localized": False, "produced_fix": False, "file_recall": 0.0,
                   "file_precision": 0.0, "exact_file_set": False}
        eval_path.parent.mkdir(parents=True, exist_ok=True)
        eval_path.write_text(json.dumps(res, indent=2))
        results.append(res)
        print(f"  → localized={res['localized']} recall={res.get('file_recall')} "
              f"precision={res.get('file_precision')} produced_fix={res.get('produced_fix')}")

    _report(results)


def _report(results: list[dict]):
    ok = [r for r in results if r.get("status") == "ok"]
    n = len(results)
    nok = len(ok) or 1
    agg = {
        "cases": n,
        "completed": len(ok),
        "errors": sum(1 for r in results if str(r.get("status", "")).startswith("error")),
        "produced_fix_rate": round(sum(r.get("produced_fix", False) for r in ok) / nok, 3),
        "localized_rate": round(sum(r.get("localized", False) for r in ok) / nok, 3),
        "exact_file_set_rate": round(sum(r.get("exact_file_set", False) for r in ok) / nok, 3),
        "mean_file_recall": round(sum(r.get("file_recall", 0) for r in ok) / nok, 3),
        "mean_file_precision": round(sum(r.get("file_precision", 0) for r in ok) / nok, 3),
    }
    (ROOT / "eval" / "swe_results.json").write_text(
        json.dumps({"aggregate": agg, "cases": results}, indent=2))

    lines = ["# SWE-style Evaluation Results\n",
             f"Cobra, {n} held-out cases (agent run at each pre-fix commit, diff vs. real PR).\n",
             "## Aggregate\n",
             "| metric | value |", "|---|---|"]
    for k, v in agg.items():
        lines.append(f"| {k} | {v} |")
    lines += ["\n## Per-case\n",
              "| issue | localized | recall | precision | exact-files | fix? | gold src | our src |",
              "|---|---|---|---|---|---|---|---|"]
    for r in sorted(results, key=lambda r: r.get("issue_number", 0)):
        lines.append(
            f"| #{r.get('issue_number')} | {'✅' if r.get('localized') else '❌'} | "
            f"{r.get('file_recall')} | {r.get('file_precision')} | "
            f"{'✅' if r.get('exact_file_set') else ''} | {'✅' if r.get('produced_fix') else '❌'} | "
            f"{', '.join(r.get('gold_src_files', []))} | {', '.join(r.get('our_src_files', []))} |")
    (ROOT / "eval" / "SWE_RESULTS.md").write_text("\n".join(lines))

    print(f"\n{'='*70}\nAGGREGATE\n{'='*70}")
    for k, v in agg.items():
        print(f"  {k:24s} {v}")
    print("\nWrote eval/swe_results.json and eval/SWE_RESULTS.md")


if __name__ == "__main__":
    main()
