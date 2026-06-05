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
            v = v.split("#")[0].strip()  # strip inline comments
            os.environ.setdefault(k.strip(), v)

PROVIDER_DEFAULTS = {
    "anthropic": "claude-sonnet-4-6",
    "groq":      "llama-3.3-70b-versatile",
    "azure":     "gpt-4o",          # overridden by AZURE_OPENAI_DEPLOYMENT if set
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
    type=click.Choice(["anthropic", "groq", "azure"], case_sensitive=False),
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
    key_map = {
        "anthropic": ("ANTHROPIC_API_KEY", None),
        "groq":      ("GROQ_API_KEY", None),
        "azure":     ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"),
    }
    required_keys = key_map[provider]
    missing = [k for k in required_keys if k and not os.environ.get(k)]
    if missing:
        for k in missing:
            click.echo(f"Error: {k} is not set. Add it to your .env file.", err=True)
        if provider == "azure":
            click.echo(
                "\nAzure requires:\n"
                "  AZURE_OPENAI_API_KEY      — your key\n"
                "  AZURE_OPENAI_ENDPOINT     — https://<resource>.openai.azure.com/\n"
                "  AZURE_OPENAI_DEPLOYMENT   — deployment name (e.g. gpt-4o)\n"
                "  AZURE_OPENAI_API_VERSION  — optional, default 2024-02-01",
                err=True,
            )
        sys.exit(1)

    from agent.github_client import fetch_issue
    from agent.pipeline import run_issue

    click.echo(f"Provider: {provider}  Model: {resolved_model}")
    click.echo(f"Fetching issue: {issue}")
    try:
        issue_obj = fetch_issue(issue, default_repo=repo)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(f"  #{issue_obj.number}: {issue_obj.title}")
    click.echo(f"  Labels: {', '.join(issue_obj.labels) or '(none)'}\n")

    try:
        run_issue(
            issue_obj,
            clone_dir=workspace,
            output_dir=output,
            provider=provider,
            model=resolved_model,
        )
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
