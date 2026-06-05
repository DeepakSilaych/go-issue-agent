#!/usr/bin/env python3
"""
Build a SWE-bench-style evaluation dataset from a repo's git history.

For each merged fix that closes an issue, record:
  - the linked issue (number + fetched title/body),
  - the fix commit and its PARENT (= the repository state *before* the fix),
  - the gold changed files + the gold diff.

The evaluator (swe_eval.py) then checks out the parent commit, runs the agent, and
compares the agent's diff against the gold one.

Usage:
    python -m eval.build_swe_dataset --repo spf13/cobra --n 30
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from agent.github_client import fetch_issue

ROOT = Path(__file__).parent.parent
SEP_FIELD, SEP_REC = "\x1f", "\x1e"


def git(repo_path, *args, text=True):
    return subprocess.run(["git", "-C", repo_path, *args], capture_output=True, text=text).stdout


def changed_files(repo_path, sha):
    out = git(repo_path, "show", "--name-only", "--format=", sha)
    return [f for f in out.splitlines() if f.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="spf13/cobra")
    ap.add_argument("--workspace", default="./workspace")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--scan", type=int, default=600, help="commits of history to scan")
    ap.add_argument("--max-files", type=int, default=5, help="skip fixes touching more files")
    args = ap.parse_args()

    repo_path = str(Path(args.workspace) / args.repo.split("/")[-1])
    if not Path(repo_path).exists():
        print(f"Repo not cloned at {repo_path}. Run build_index/solve first.", file=sys.stderr)
        sys.exit(1)
    git(repo_path, "checkout", "--force", "main")

    log = git(repo_path, "log", "--no-merges",
              f"--format=%H{SEP_FIELD}%s{SEP_FIELD}%b{SEP_REC}", f"-n{args.scan}")
    records = [r for r in log.split(SEP_REC) if r.strip()]

    cases, seen_issue = [], set()
    for rec in records:
        parts = rec.strip().split(SEP_FIELD)
        if len(parts) < 2:
            continue
        sha, subject = parts[0].strip(), parts[1]
        body = parts[2] if len(parts) > 2 else ""

        m_issue = re.search(r"(?:closes?|fixes?|resolves?)\s+#(\d+)", subject + "\n" + body, re.I)
        if not m_issue:
            continue
        issue_number = int(m_issue.group(1))
        if issue_number in seen_issue:
            continue

        m_pr = re.search(r"\(#(\d+)\)\s*$", subject)
        pr_number = int(m_pr.group(1)) if m_pr else None

        files = changed_files(repo_path, sha)
        go_files = [f for f in files if f.endswith(".go")]
        src = [f for f in go_files if not f.endswith("_test.go")]
        if not src:                       # need at least one Go source change
            continue
        if len(files) > args.max_files:   # keep it small/medium
            continue

        base = git(repo_path, "rev-parse", f"{sha}^").strip()
        if not base:
            continue
        gold_diff = git(repo_path, "show", "--format=", sha)

        seen_issue.add(issue_number)
        cases.append({
            "repo": args.repo,
            "issue_number": issue_number,
            "pr_number": pr_number,
            "fix_commit": sha,
            "base_commit": base,
            "gold_files": files,
            "gold_go_files": go_files,
            "gold_src_files": src,
            "gold_diff": gold_diff[:20000],
            "subject": subject,
        })
        print(f"  + #{issue_number:<5} base={base[:10]} files={len(files)} src={src}")
        if len(cases) >= args.n:
            break

    print(f"\nCollected {len(cases)} candidate cases; fetching issue text...")
    final = []
    for c in cases:
        try:
            iss = fetch_issue(str(c["issue_number"]), default_repo=args.repo)
            c["issue_title"], c["issue_body"] = iss.title, (iss.body or "")[:2000]
            final.append(c)
            print(f"  fetched #{c['issue_number']}: {iss.title[:60]}")
        except Exception as e:
            print(f"  skip #{c['issue_number']} (fetch failed: {type(e).__name__})")

    out = ROOT / "eval" / "swe_dataset.json"
    out.write_text(json.dumps(final, indent=2))
    print(f"\nWrote {len(final)} cases to {out}")


if __name__ == "__main__":
    main()
