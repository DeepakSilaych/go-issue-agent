"""GitHub API client for fetching issues."""

import os
import re
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import requests


@dataclass
class Issue:
    number: int
    title: str
    body: str
    labels: list[str]
    url: str
    repo: str  # owner/name


@dataclass
class PullRequest:
    number: int
    title: str
    body: str
    url: str
    repo: str            # owner/name
    diff: str            # unified diff
    changed_files: list[str]
    base_sha: str        # commit the PR branched from
    issue_number: Optional[int]  # linked issue, if any (Fixes/Closes #N)


def _gh_headers(token: Optional[str], accept: str = "application/vnd.github.v3+json") -> dict:
    h = {"Accept": accept}
    if token:
        h["Authorization"] = f"token {token}"
    return h


def _gh_get(url: str, token: Optional[str], accept: str = "application/vnd.github.v3+json", **kwargs):
    """GET with token, retrying unauthenticated if the token is rejected (401)."""
    resp = requests.get(url, headers=_gh_headers(token, accept), **kwargs)
    if resp.status_code == 401 and token:
        resp = requests.get(url, headers=_gh_headers(None, accept), **kwargs)
    return resp


def fetch_pr(pr_ref: str, default_repo: str = "spf13/cobra") -> PullRequest:
    """
    Fetch a GitHub pull request (metadata + unified diff + linked issue).

    pr_ref can be a full PR URL (.../pull/N) or a bare number (uses default_repo).
    """
    repo, number = default_repo, None
    m = re.match(r"https://github\.com/([^/]+/[^/]+)/pull/(\d+)", pr_ref)
    if m:
        repo, number = m.group(1), int(m.group(2))
    elif pr_ref.isdigit():
        number = int(pr_ref)
    else:
        raise ValueError(f"Cannot parse PR reference: {pr_ref!r}\nExpected a GitHub PR URL or a number.")

    token = os.environ.get("GITHUB_TOKEN")
    api = f"https://api.github.com/repos/{repo}/pulls/{number}"

    meta_resp = _gh_get(api, token, timeout=30)
    if meta_resp.status_code == 404:
        raise ValueError(f"PR #{number} not found in {repo}")
    meta_resp.raise_for_status()
    meta = meta_resp.json()

    diff_resp = _gh_get(api, token, "application/vnd.github.v3.diff", timeout=30)
    diff = diff_resp.text if diff_resp.status_code == 200 else "(could not fetch diff)"

    files_resp = _gh_get(api + "/files", token, params={"per_page": 100}, timeout=30)
    changed = [f["filename"] for f in files_resp.json()] if files_resp.status_code == 200 else []

    body = meta.get("body") or ""
    im = re.search(r"(?:closes?|fixes?|resolves?)\s+#(\d+)", body, re.IGNORECASE)
    issue_number = int(im.group(1)) if im else None

    return PullRequest(
        number=number,
        title=meta.get("title", ""),
        body=body,
        url=meta.get("html_url", f"https://github.com/{repo}/pull/{number}"),
        repo=repo,
        diff=diff,
        changed_files=changed,
        base_sha=meta.get("base", {}).get("sha", ""),
        issue_number=issue_number,
    )


def fetch_issue(issue_ref: str, default_repo: str = "spf13/cobra") -> Issue:
    """
    Fetch a GitHub issue.

    issue_ref can be:
      - A full URL: https://github.com/spf13/cobra/issues/2123
      - An issue number: "2123" (uses default_repo)
    """
    repo = default_repo
    number = None

    url_match = re.match(
        r"https://github\.com/([^/]+/[^/]+)/issues/(\d+)", issue_ref
    )
    if url_match:
        repo = url_match.group(1)
        number = int(url_match.group(2))
    elif issue_ref.isdigit():
        number = int(issue_ref)
    else:
        raise ValueError(
            f"Cannot parse issue reference: {issue_ref!r}\n"
            "Expected a GitHub issue URL or a number."
        )

    token = os.environ.get("GITHUB_TOKEN")
    base_headers = {"Accept": "application/vnd.github.v3+json"}
    api_url = f"https://api.github.com/repos/{repo}/issues/{number}"

    # Try with token first, fall back to unauthenticated on 401
    for headers in (
        {**base_headers, "Authorization": f"token {token}"} if token else None,
        base_headers,
    ):
        if headers is None:
            continue
        resp = requests.get(api_url, headers=headers, timeout=30)
        if resp.status_code == 401:
            continue  # token invalid, retry without
        break

    if resp.status_code == 404:
        raise ValueError(f"Issue #{number} not found in {repo}")
    if resp.status_code in (403, 429):
        print("  GitHub API rate limit hit, falling back to HTML scrape...")
        return _fetch_issue_html(repo, number)
    resp.raise_for_status()

    data = resp.json()
    return Issue(
        number=number,
        title=data["title"],
        body=data.get("body") or "",
        labels=[lbl["name"] for lbl in data.get("labels", [])],
        url=data["html_url"],
        repo=repo,
    )


def _fetch_issue_html(repo: str, number: int) -> Issue:
    """Scrape issue title and body from GitHub HTML when API is rate-limited."""
    url = f"https://github.com/{repo}/issues/{number}"
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    html = resp.text

    # Title
    title_match = re.search(r'<bdi class="js-issue-title[^"]*"[^>]*>\s*(.*?)\s*</bdi>', html)
    if not title_match:
        title_match = re.search(r'<title>\s*(.*?)\s*·', html)
    title = title_match.group(1).strip() if title_match else f"Issue #{number}"
    title = re.sub(r'<[^>]+>', '', title).strip()

    # Body — grab the first comment's markdown content
    body_match = re.search(
        r'<div class="[^"]*comment-body[^"]*"[^>]*>\s*<div[^>]*>\s*(.*?)\s*</div>',
        html, re.DOTALL
    )
    if body_match:
        raw = body_match.group(1)
        # Strip HTML tags crudely
        body = re.sub(r'<[^>]+>', ' ', raw)
        body = re.sub(r'\s+', ' ', body).strip()
    else:
        body = "(could not parse issue body)"

    return Issue(
        number=number,
        title=title,
        body=body,
        labels=[],
        url=url,
        repo=repo,
    )


def clone_or_update_repo(repo: str, clone_dir: str) -> str:
    """
    Clone a GitHub repo into clone_dir/{repo_name} if not present,
    or fetch the latest refs if it already exists. Returns the repo path.

    We `fetch` (not `pull`) on purpose: create_fix_branch resets the working
    tree to the default branch tip, so we only need up-to-date remote refs.
    """
    repo_name = repo.split("/")[-1]
    target = Path(clone_dir) / repo_name

    if target.exists():
        print(f"  Repo exists at {target}, fetching latest...")
        subprocess.run(
            ["git", "fetch", "--quiet", "origin"],
            cwd=str(target),
            check=False,
            capture_output=True,
        )
        # Clean up any worktrees left behind by a crashed prior run.
        subprocess.run(["git", "worktree", "prune"], cwd=str(target), capture_output=True)
    else:
        print(f"  Cloning {repo} into {target}...")
        target.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://github.com/{repo}.git"
        subprocess.run(["git", "clone", "--quiet", url, str(target)], check=True)

    return str(target)


def default_branch(repo_path: str) -> str:
    """Return the repo's default branch name (e.g. 'main' or 'master')."""
    result = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().split("/")[-1]  # "origin/main" -> "main"

    # Fallback: probe common names.
    for name in ("main", "master"):
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"origin/{name}"],
            cwd=repo_path,
            capture_output=True,
        )
        if probe.returncode == 0:
            return name
    return "main"


def create_fix_branch(repo_path: str, issue_number: int, base_commit: Optional[str] = None) -> str:
    """
    Create a clean fix branch for the issue, reset to a known base.

    base_commit: if given (a commit SHA, tag, or ref), the fix branch is created from
    that exact point — use this to solve an issue at the repository state *before* its
    fix landed (fair, SWE-bench-style evaluation). If omitted, resets to the default
    branch tip.

    This is idempotent: re-running on the same issue discards any changes from a prior
    run instead of accumulating on top of them, so diffs are reproducible.
    """
    branch = f"fix/issue-{issue_number}"
    base_ref = base_commit if base_commit else f"origin/{default_branch(repo_path)}"

    # Discard uncommitted state, then recreate the branch from the pinned base.
    subprocess.run(["git", "checkout", "--force", base_ref], cwd=repo_path, capture_output=True)
    subprocess.run(["git", "reset", "--hard", base_ref], cwd=repo_path, capture_output=True)
    subprocess.run(["git", "clean", "-fd"], cwd=repo_path, capture_output=True)
    subprocess.run(["git", "checkout", "-B", branch], cwd=repo_path, capture_output=True)
    return branch
