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
    or pull latest changes if it already exists. Returns the repo path.
    """
    repo_name = repo.split("/")[-1]
    target = Path(clone_dir) / repo_name

    if target.exists():
        print(f"  Repo exists at {target}, pulling latest...")
        subprocess.run(
            ["git", "pull", "--quiet"],
            cwd=str(target),
            check=False,
            capture_output=True,
        )
    else:
        print(f"  Cloning {repo} into {target}...")
        target.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://github.com/{repo}.git"
        subprocess.run(["git", "clone", "--quiet", url, str(target)], check=True)

    return str(target)


def create_fix_branch(repo_path: str, issue_number: int) -> str:
    """Create and checkout a branch for the fix. Returns branch name."""
    branch = f"fix/issue-{issue_number}"
    result = subprocess.run(
        ["git", "checkout", "-b", branch],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Branch may already exist
        subprocess.run(
            ["git", "checkout", branch],
            cwd=repo_path,
            capture_output=True,
        )
    return branch
