#!/usr/bin/env python3
"""
go-issue-agent: Agentic AI contributor for open-source Go projects.

Three modes:

    # 1) EXPLAIN — explain the issue and the proposed solution (no code changes)
    python solve.py explain --issue https://github.com/spf13/cobra/issues/2396

    # 2) IMPLEMENT — localize, plan, patch, validate, write a PR summary
    python solve.py implement --issue https://github.com/spf13/cobra/issues/2396
    python solve.py implement --issue 2396 --guidance "use a three-index slice instead of copy"

    # 3) REVIEW — review a pull request against its issue + project conventions
    python solve.py review --pr https://github.com/spf13/cobra/pull/2356

Providers: anthropic (default) | groq | azure.
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

# Sanitize provider config: `docker --env-file` (unlike our .env loader above) does NOT
# strip inline comments or surrounding whitespace, which silently breaks e.g. the Azure
# api-version. None of these values legitimately contain '#', so this is safe.
for _k in ("ANTHROPIC_API_KEY", "GROQ_API_KEY", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT",
           "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_VERSION", "GITHUB_TOKEN"):
    _v = os.environ.get(_k)
    if _v:
        os.environ[_k] = _v.split("#")[0].strip()

PROVIDER_DEFAULTS = {
    "anthropic": "claude-sonnet-4-6",
    "groq":      "llama-3.3-70b-versatile",
    "azure":     "gpt-4o",          # overridden by AZURE_OPENAI_DEPLOYMENT if set
}


def _resolve_model(provider: str, model: str) -> str:
    provider = provider.lower()
    if provider == "azure":
        return model or os.environ.get("AZURE_OPENAI_DEPLOYMENT") or PROVIDER_DEFAULTS[provider]
    return model or PROVIDER_DEFAULTS[provider]


def _check_keys(provider: str):
    """Exit with a helpful message if the provider's required env vars are missing."""
    key_map = {
        "anthropic": ("ANTHROPIC_API_KEY", None),
        "groq":      ("GROQ_API_KEY", None),
        "azure":     ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"),
    }
    missing = [k for k in key_map[provider] if k and not os.environ.get(k)]
    if missing:
        for k in missing:
            click.echo(f"Error: {k} is not set. Add it to your .env file.", err=True)
        if provider == "azure":
            click.echo(
                "\nAzure requires AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, and "
                "AZURE_OPENAI_DEPLOYMENT (the deployment name).", err=True,
            )
        sys.exit(1)


def _llm_options(f):
    """Shared --provider/--model/--workspace/--output options."""
    f = click.option("--provider", default="anthropic", show_default=True,
                     type=click.Choice(["anthropic", "groq", "azure"], case_sensitive=False),
                     help="LLM provider.")(f)
    f = click.option("--model", default=None,
                     help="Model name (or Azure deployment). Provider-specific default.")(f)
    f = click.option("--workspace", default="./workspace", show_default=True,
                     help="Directory where repos are cloned.")(f)
    f = click.option("--output", default="./output", show_default=True,
                     help="Directory for output artifacts.")(f)
    return f


@click.group()
def cli():
    """Agentic assistant for open-source Go issues: explain, implement, or review."""


# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------
@cli.command()
@click.option("--issue", required=True, help="GitHub issue URL or number.")
@click.option("--repo", default="spf13/cobra", show_default=True, help="owner/name (for bare numbers).")
@click.option("--base-commit", default=None, help="Analyze at this commit (pre-fix state).")
@_llm_options
def explain(issue, repo, base_commit, provider, model, workspace, output):
    """Explain a GitHub issue and the proposed solution (no code changes)."""
    provider = provider.lower()
    resolved = _resolve_model(provider, model)
    _check_keys(provider)

    from agent.github_client import fetch_issue
    from agent.pipeline import explain_issue

    click.echo(f"[explain] Provider: {provider}  Model: {resolved}")
    issue_obj = _fetch_issue_or_exit(fetch_issue, issue, repo)
    _run(lambda: explain_issue(issue_obj, clone_dir=workspace, output_dir=output,
                               provider=provider, model=resolved, base_commit=base_commit))


# ---------------------------------------------------------------------------
# implement
# ---------------------------------------------------------------------------
@cli.command()
@click.option("--issue", required=True, help="GitHub issue URL or number.")
@click.option("--repo", default="spf13/cobra", show_default=True, help="owner/name (for bare numbers).")
@click.option("--base-commit", default=None, help="Solve at this commit (pre-fix state, fair eval).")
@click.option("--guidance", default=None, help="Steer toward a preferred/modified solution.")
@_llm_options
def implement(issue, repo, base_commit, guidance, provider, model, workspace, output):
    """Localize, plan, patch, validate, and write a PR summary."""
    provider = provider.lower()
    resolved = _resolve_model(provider, model)
    _check_keys(provider)

    from agent.github_client import fetch_issue
    from agent.pipeline import run_issue

    click.echo(f"[implement] Provider: {provider}  Model: {resolved}")
    issue_obj = _fetch_issue_or_exit(fetch_issue, issue, repo)
    _run(lambda: run_issue(issue_obj, clone_dir=workspace, output_dir=output,
                           provider=provider, model=resolved,
                           base_commit=base_commit, guidance=guidance))


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------
@cli.command()
@click.option("--pr", "pr_ref", required=True, help="GitHub PR URL or number.")
@click.option("--repo", default="spf13/cobra", show_default=True, help="owner/name (for bare numbers).")
@_llm_options
def review(pr_ref, repo, provider, model, workspace, output):
    """Review a pull request against its issue and project conventions."""
    provider = provider.lower()
    resolved = _resolve_model(provider, model)
    _check_keys(provider)

    from agent.github_client import fetch_pr
    from agent.pipeline import review_pr

    click.echo(f"[review] Provider: {provider}  Model: {resolved}")
    try:
        pr_obj = fetch_pr(pr_ref, default_repo=repo)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.echo(f"  PR #{pr_obj.number}: {pr_obj.title}  ({len(pr_obj.changed_files)} files)\n")
    _run(lambda: review_pr(pr_obj, clone_dir=workspace, output_dir=output,
                           provider=provider, model=resolved))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _fetch_issue_or_exit(fetch_issue, issue, repo):
    click.echo(f"Fetching issue: {issue}")
    try:
        issue_obj = fetch_issue(issue, default_repo=repo)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.echo(f"  #{issue_obj.number}: {issue_obj.title}")
    click.echo(f"  Labels: {', '.join(issue_obj.labels) or '(none)'}\n")
    return issue_obj


def _run(fn):
    try:
        fn()
    except KeyboardInterrupt:
        click.echo("\nInterrupted.", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"\nError: {type(e).__name__}: {e}", err=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    cli()
