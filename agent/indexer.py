"""
Offline repo indexer — run once per repo via build_index.py.

Builds:
  indexes/{repo_name}/symbols.json   — symbol index (func/type/const/var)
  indexes/{repo_name}/pr_examples.json — recent merged PRs with issue→diff
"""

import json
import re
from pathlib import Path
from typing import Optional

import requests


# ---------------------------------------------------------------------------
# Symbol indexing
# ---------------------------------------------------------------------------

def build_symbol_index(repo_path: str) -> list[dict]:
    """
    Extract all top-level symbols from Go files with signatures and comments.
    Returns a list of symbol dicts.
    """
    root = Path(repo_path).resolve()
    symbols = []

    excluded = {"vendor", "testdata", ".git", "node_modules"}

    for go_file in sorted(root.rglob("*.go")):
        parts = set(go_file.relative_to(root).parts[:-1])
        if parts & excluded:
            continue

        rel = str(go_file.relative_to(root))
        symbols.extend(_extract_file_symbols(go_file, rel))

    return symbols


def _extract_file_symbols(filepath: Path, rel_path: str) -> list[dict]:
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return []

    lines = content.splitlines()
    symbols = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Capture preceding comment block
        comment_lines = []
        j = i - 1
        while j >= 0 and lines[j].strip().startswith("//"):
            comment_lines.insert(0, lines[j].strip().lstrip("/").strip())
            j -= 1
        comment = " ".join(comment_lines[:2])  # first 2 comment lines

        # Match top-level declarations
        for pattern, kind in [
            (r"^func\s+\((\w+)\s+\*?(\w+)\)\s+(\w+)\s*(\([^)]*\).*)", "method"),
            (r"^func\s+(\w+)\s*(\([^)]*\).*)",                          "func"),
            (r"^type\s+(\w+)\s+(struct|interface|\S+)",                   "type"),
            (r"^var\s+(\w+)\s+",                                          "var"),
            (r"^const\s+(\w+)\s+",                                        "const"),
        ]:
            m = re.match(pattern, line)
            if m:
                sig = line.rstrip()
                if len(sig) > 120:
                    sig = sig[:120] + " ..."
                name = m.group(3) if kind == "method" else m.group(1)
                symbols.append({
                    "file": rel_path,
                    "line": i + 1,
                    "kind": kind,
                    "name": name,
                    "signature": sig,
                    "comment": comment,
                })
                break

        i += 1

    return symbols


# ---------------------------------------------------------------------------
# PR examples
# ---------------------------------------------------------------------------

def fetch_pr_examples(repo: str, n: int = 50, github_token: Optional[str] = None) -> list[dict]:
    """
    Fetch recent merged PRs from GitHub.
    For each PR, try to link to the issue it closes and grab the diff summary.
    """
    headers = {"Accept": "application/vnd.github.v3+json"}
    if github_token:
        headers["Authorization"] = f"token {github_token}"

    # Fetch merged PRs
    prs_url = f"https://api.github.com/repos/{repo}/pulls"
    resp = requests.get(prs_url, headers=headers, params={"state": "closed", "per_page": n}, timeout=30)
    if resp.status_code != 200:
        print(f"  Warning: could not fetch PRs ({resp.status_code})")
        return []

    examples = []
    for pr in resp.json():
        if not pr.get("merged_at"):
            continue

        # Extract issue number from PR body
        issue_number = None
        issue_title = ""
        issue_body = ""
        body = pr.get("body") or ""
        m = re.search(r"(?:closes?|fixes?|resolves?)\s+#(\d+)", body, re.IGNORECASE)
        if m:
            issue_number = int(m.group(1))
            issue_data = _fetch_issue_data(repo, issue_number, headers)
            if issue_data:
                issue_title = issue_data.get("title", "")
                issue_body = (issue_data.get("body") or "")[:500]

        # Get diff summary (file names changed)
        diff_summary = _fetch_pr_files(repo, pr["number"], headers)

        examples.append({
            "pr_number": pr["number"],
            "pr_title": pr.get("title", ""),
            "pr_body": body[:400],
            "issue_number": issue_number,
            "issue_title": issue_title,
            "issue_body": issue_body,
            "diff_summary": diff_summary,
            "url": pr.get("html_url", ""),
        })

    print(f"  Fetched {len(examples)} PR examples")
    return examples


def _fetch_issue_data(repo: str, number: int, headers: dict) -> Optional[dict]:
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/issues/{number}",
            headers=headers, timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def _fetch_pr_files(repo: str, pr_number: int, headers: dict) -> str:
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/pulls/{pr_number}/files",
            headers=headers, timeout=10, params={"per_page": 10},
        )
        if resp.status_code == 200:
            files = resp.json()
            lines = []
            for f in files[:8]:
                status = f.get("status", "modified")
                fname = f.get("filename", "")
                additions = f.get("additions", 0)
                deletions = f.get("deletions", 0)
                lines.append(f"  {status}: {fname} (+{additions}/-{deletions})")
            return "\n".join(lines)
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Save / load index
# ---------------------------------------------------------------------------

def save_index(repo: str, symbols: list[dict], pr_examples: list[dict], base_dir: str = "indexes"):
    repo_name = repo.replace("/", "_")
    out_dir = Path(base_dir) / repo_name
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "symbols.json").write_text(json.dumps(symbols, indent=2))
    (out_dir / "pr_examples.json").write_text(json.dumps(pr_examples, indent=2))

    print(f"  Saved index to {out_dir}/")
    print(f"    symbols:      {len(symbols)}")
    print(f"    PR examples:  {len(pr_examples)}")


def load_index(repo: str, base_dir: str = "indexes") -> dict:
    repo_name = repo.replace("/", "_")
    idx_dir = Path(base_dir) / repo_name

    def _load(name):
        p = idx_dir / name
        if p.exists():
            return json.loads(p.read_text())
        return []

    return {
        "symbols": _load("symbols.json"),
        "pr_examples": _load("pr_examples.json"),
    }


def index_exists(repo: str, base_dir: str = "indexes") -> bool:
    repo_name = repo.replace("/", "_")
    return (Path(base_dir) / repo_name / "symbols.json").exists()
