#!/usr/bin/env python3
"""
Build the offline knowledge index for a repo.

Run once before using solve.py — gives the agent:
  - AST symbol index (every func/type with location)
  - Recent PR examples (for style matching)
  - Test helper catalog

Usage:
    python build_index.py --repo spf13/cobra
    python build_index.py --repo gin-gonic/gin
"""

import os
from pathlib import Path

import click

env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.split("#")[0].strip()
            os.environ.setdefault(k.strip(), v)


@click.command()
@click.option("--repo", default="spf13/cobra", show_default=True, help="GitHub repo owner/name")
@click.option("--workspace", default="./workspace", show_default=True, help="Where repos are cloned")
@click.option("--index-dir", default="./indexes", show_default=True, help="Where to write the index")
@click.option("--pr-count", default=50, show_default=True, help="Number of recent PRs to index")
def main(repo: str, workspace: str, index_dir: str, pr_count: int):
    """Build offline knowledge index for a GitHub repo."""
    from agent.github_client import clone_or_update_repo
    from agent.indexer import build_symbol_index, fetch_pr_examples, save_index

    click.echo(f"Building index for {repo}...")

    # Clone / update
    click.echo("\n[1/3] Cloning/updating repo...")
    repo_path = clone_or_update_repo(repo, workspace)

    # Symbol index
    click.echo("\n[2/3] Extracting Go symbols...")
    symbols = build_symbol_index(repo_path)
    click.echo(f"  Found {len(symbols)} symbols")

    # PR examples
    click.echo(f"\n[3/3] Fetching {pr_count} recent PRs from GitHub...")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        click.echo("  Note: no GITHUB_TOKEN set — may hit rate limits")
    pr_examples = fetch_pr_examples(repo, n=pr_count, github_token=token)

    # Save
    click.echo("\nSaving index...")
    save_index(repo, symbols, pr_examples, base_dir=index_dir)
    click.echo(f"\nDone. Run: python solve.py implement --issue <N> --repo {repo}")


if __name__ == "__main__":
    main()
