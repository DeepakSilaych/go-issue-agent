#!/usr/bin/env python3
"""
Localization-recall evaluation harness.

Measures how well the *retrieval* layer (BM25 over the symbol index) finds the files
a real merged PR actually changed — i.e. "do we surface the right code to edit?".
This is the deterministic, no-API-key part of localization; if retrieval feeds the
LLM the right files, the LLM has what it needs, and if it doesn't, the LLM hallucinates.

For each case in dataset.json we run `retrieve_symbols(issue_text)` and check whether
the PR's changed Go files appear in the retrieved set, and at what rank.

Usage:
    python -m eval.localization_eval                 # default top_k=25
    python -m eval.localization_eval --top-k 15
    python -m eval.localization_eval --repo spf13/cobra

Fully offline: uses the shipped index in indexes/{repo} and the embedded issue text
in dataset.json. No network, no API key.
"""

import argparse
import json
from pathlib import Path

from agent.indexer import load_index
from agent.retrieval import retrieve_symbols

ROOT = Path(__file__).parent.parent


def _ranked_files(symbols: list[dict]) -> list[str]:
    """Distinct files in retrieval-rank order."""
    out: list[str] = []
    for s in symbols:
        if s["file"] not in out:
            out.append(s["file"])
    return out


def evaluate(repo: str, top_k: int) -> dict:
    dataset = json.loads((ROOT / "eval" / "dataset.json").read_text())
    cases = [c for c in dataset if c["repo"] == repo]
    index = load_index(repo)
    symbols_index = index["symbols"]

    code_cases = [c for c in cases if c["kind"] == "code"]
    docs_cases = [c for c in cases if c["kind"] == "docs"]

    print(f"\nLocalization recall — {repo}  (top_k={top_k}, {len(symbols_index)} symbols indexed)")
    print(f"{'issue':>7}  {'held-out':>8}  {'src-recall':>10}  files (changed → retrieval rank)")
    print("-" * 96)

    total_src_hit = total_src = 0
    for c in sorted(code_cases, key=lambda c: c["issue_number"]):
        syms = retrieve_symbols(f"{c['title']} {c['body']}", symbols_index, top_k=top_k)
        ranked = _ranked_files(syms)
        details = []
        for f in c["ground_truth_go_files"]:
            rank = ranked.index(f) + 1 if f in ranked else None
            details.append(f"{f}→{('#'+str(rank)) if rank else 'MISS'}")
            if not f.endswith("_test.go"):
                total_src += 1
                total_src_hit += 1 if rank else 0
        srcs = [f for f in c["ground_truth_go_files"] if not f.endswith("_test.go")]
        src_recall = sum(1 for f in srcs if f in ranked) / len(srcs) if srcs else 0.0
        flag = "YES" if c["held_out"] else ""
        print(f"#{c['issue_number']:>6}  {flag:>8}  {src_recall:>9.0%}   {', '.join(details)}")

    print("-" * 96)
    src_recall = total_src_hit / total_src if total_src else 0.0
    print(f"Source-file recall (the file that MUST be edited): {total_src_hit}/{total_src} = {src_recall:.0%}")

    # Docs cases: the correct behaviour is to surface NO Go file (Go-only index).
    if docs_cases:
        print("\nNon-code (docs) cases — correct behaviour is to surface no Go source file:")
        for c in sorted(docs_cases, key=lambda c: c["issue_number"]):
            syms = retrieve_symbols(f"{c['title']} {c['body']}", symbols_index, top_k=top_k)
            ranked = _ranked_files(syms)
            go_src = [f for f in ranked if f.endswith(".go") and not f.endswith("_test.go")]
            print(f"  #{c['issue_number']:>5}  {c['title'][:48]:48s}  → retrieval still returns Go files "
                  f"({len(go_src)}); pipeline needs a 'non-code issue' guard")

    return {"source_recall": src_recall, "n_code": len(code_cases), "n_docs": len(docs_cases), "top_k": top_k}


def main():
    ap = argparse.ArgumentParser(description="Localization recall eval")
    ap.add_argument("--repo", default="spf13/cobra")
    ap.add_argument("--top-k", type=int, default=25)
    args = ap.parse_args()
    metrics = evaluate(args.repo, args.top_k)
    print(f"\nSummary: {json.dumps(metrics)}")


if __name__ == "__main__":
    main()
