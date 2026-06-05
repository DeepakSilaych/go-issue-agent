#!/usr/bin/env python3
"""
go-issue-agent: Agentic AI contributor for open-source Go projects.

Usage:
    python solve.py --issue https://github.com/spf13/cobra/issues/2123
    python solve.py --issue 2123 --repo spf13/cobra
"""

import os
import sys
from pathlib import Path

import click

# Load .env if present
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


@click.command()
@click.option(
    "--issue",
    required=True,
    help="GitHub issue URL (https://github.com/owner/repo/issues/N) or issue number.",
)
@click.option(
    "--repo",
    default="spf13/cobra",
    show_default=True,
    help="GitHub repo (owner/name). Used when --issue is a bare number.",
)
@click.option(
    "--workspace",
    default="./workspace",
    show_default=True,
    help="Directory where repos are cloned.",
)
@click.option(
    "--output",
    default="./output",
    show_default=True,
    help="Directory for output artifacts (diff, PR summary, JSON).",
)
@click.option(
    "--model",
    default="claude-sonnet-4-6",
    show_default=True,
    help="Claude model to use.",
)
def main(issue: str, repo: str, workspace: str, output: str, model: str):
    """
    Solve a GitHub issue from an open-source Go project.

    The agent will:
    \b
    1. Clone the repository (or use an existing clone)
    2. Fetch the issue from GitHub
    3. Build a repository map
    4. Identify relevant files (localize)
    5. Plan a targeted fix
    6. Implement the fix via a tool-use loop
    7. Run go build and go test to validate
    8. Generate a PR title and body

    Output is written to --output/issue-{N}/:
      - pr_summary.md  (PR title + body)
      - changes.patch  (git diff)
      - result.json    (full structured result)
    """
    # Validate API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        click.echo(
            "Error: ANTHROPIC_API_KEY is not set.\n"
            "Set it in your environment or create a .env file.",
            err=True,
        )
        sys.exit(1)

    from agent.github_client import fetch_issue
    from agent.pipeline import Pipeline

    # Override model if specified
    if model != "claude-sonnet-4-6":
        import agent.pipeline as pipeline_module
        pipeline_module.MODEL = model

    click.echo(f"Fetching issue: {issue}")
    try:
        issue_obj = fetch_issue(issue, default_repo=repo)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(f"  #{issue_obj.number}: {issue_obj.title}")
    click.echo(f"  Labels: {', '.join(issue_obj.labels) or '(none)'}\n")

    pipeline = Pipeline(repo=issue_obj.repo, clone_dir=workspace, output_dir=output)

    try:
        pipeline.run(issue_obj)
    except KeyboardInterrupt:
        click.echo("\nInterrupted.", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"\nError: {type(e).__name__}: {e}", err=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
