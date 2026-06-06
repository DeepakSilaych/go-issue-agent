#!/usr/bin/env bash
# Entrypoint for the go-issue-agent image.
#
# Bare issue (default = implement / plan+edit), produces output/issue-<N>/pr.md:
#   docker run --rm -e ANTHROPIC_API_KEY=... -v "$PWD/output:/app/output" \
#       go-issue-agent https://github.com/spf13/cobra/issues/2396
#
# Explicit modes (all of solve.py is available):
#   docker run ... go-issue-agent explain   --issue 2396 --repo spf13/cobra   # plan only
#   docker run ... go-issue-agent implement --issue 2396 --repo spf13/cobra   # plan + edit
#   docker run ... go-issue-agent review    --pr 2356    --repo spf13/cobra   # review a PR
set -euo pipefail

case "${1:-}" in
    explain|implement|review|--help|-h)
        exec python3 solve.py "$@"
        ;;
    "")
        echo "usage: docker run ... go-issue-agent <issue-url|issue#> [--repo owner/name] [--provider ...]"
        echo "   or: docker run ... go-issue-agent {explain|implement|review} [options]"
        exit 1
        ;;
    *)
        # Treat the first token as the issue; default to implement (plan + edit).
        exec python3 solve.py implement --issue "$@"
        ;;
esac
