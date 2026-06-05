#!/usr/bin/env python3
"""
go-issue-agent: Agentic AI contributor for open-source Go projects.

Usage:
    # Anthropic (default)
    python solve.py --issue https://github.com/spf13/cobra/issues/2396

    # Groq (free tier)
    python solve.py --issue 2396 --provider groq
    python solve.py --issue 2396 --provider groq --model llama-3.3-70b-versatile
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

PROVIDER_DEFAULTS = {
    "anthropic": "claude-sonnet-4-6",
    "groq":      "llama-3.3-70b-versatile",
}


@click.command()
@click.option(
    "--issue",
    required=True,
    help="GitHub issue URL (https://github.com/owner/repo/issues/N) or bare number.",
)
@click.option(
    "--repo",
    default="spf13/cobra",
    show_default=True,
    help="GitHub repo (owner/name). Used when --issue is a bare number.",
)
@click.option(
    "--provider",
    default="anthropic",
    show_default=True,
    type=click.Choice(["anthropic", "groq"], case_sensitive=False),
    help="LLM provider.",
)
@click.option(
    "--model",
    default=None,
    help=(
        "Model name. Defaults: anthropic=claude-sonnet-4-6, "
        "groq=llama-3.3-70b-versatile."
    ),
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
def main(issue: str, repo: str, provider: str, model: str, workspace: str, output: str):
    """
    Solve a GitHub issue from an open-source Go project.

    \b
    Phases:
      1. Localize  — identify relevant files from repo map + issue
      2. Plan      — generate a targeted fix strategy
      3. Patch     — implement the fix (tool-use loop: read, edit, test)
      4. Validate  — run go test, generate PR title + body

    \b
    Output (output/issue-{N}/):
      pr_summary.md   PR title and body
      changes.patch   git diff of the changes
      result.json     full structured result
    """
    provider = provider.lower()
    resolved_model = model or PROVIDER_DEFAULTS[provider]

    # Validate required API keys
    key_map = {"anthropic": "ANTHROPIC_API_KEY", "groq": "GROQ_API_KEY"}
    required_key = key_map[provider]
    if not os.environ.get(required_key):
        click.echo(
            f"Error: {required_key} is not set.\n"
            f"Add it to your .env file or export it in your shell.",
            err=True,
        )
        sys.exit(1)

    from agent.github_client import fetch_issue
    from agent.pipeline import Pipeline

    click.echo(f"Provider: {provider}  Model: {resolved_model}")
    click.echo(f"Fetching issue: {issue}")
    try:
        issue_obj = fetch_issue(issue, default_repo=repo)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(f"  #{issue_obj.number}: {issue_obj.title}")
    click.echo(f"  Labels: {', '.join(issue_obj.labels) or '(none)'}\n")

    pipeline = Pipeline(
        repo=issue_obj.repo,
        clone_dir=workspace,
        output_dir=output,
        provider=provider,
        model=resolved_model,
    )

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
