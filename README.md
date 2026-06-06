# go-issue-agent

An agentic assistant for GitHub issues in open-source Go projects. You give it an issue and it
finds the relevant code, fixes it, runs the tests, and writes a pull request: title,
description, and diff.

It has three modes:

- `explain`: explain the issue and propose a fix, without changing any code
- `implement`: localize, patch, validate, then write a PR (title, description, diff)
- `review`: review a pull request against its issue and the project's conventions

It runs on LangGraph and LangChain. [ARCHITECTURE.md](ARCHITECTURE.md) covers how it works.

## Quick start (Docker)

The image bundles Python, the Go toolchain, and pre-built indexes for cobra and validator, so
one command takes an issue and writes a Markdown file with the PR title, description, and diff.

```bash
docker build -t go-issue-agent .

# Issue (URL or number) -> output/issue-<N>/pr.md
docker run --rm --env-file .env -v "$PWD/output:/app/output" \
  go-issue-agent https://github.com/spf13/cobra/issues/2396
```

All three modes work through the image:

```bash
docker run --rm --env-file .env -v "$PWD/output:/app/output" go-issue-agent explain   --issue 2396 --repo spf13/cobra
docker run --rm --env-file .env -v "$PWD/output:/app/output" go-issue-agent implement --issue 2396 --repo spf13/cobra
docker run --rm --env-file .env -v "$PWD/output:/app/output" go-issue-agent review    --pr   2356 --repo spf13/cobra
```

A bare issue defaults to `implement`. Add `--provider groq` or `--provider azure` to switch
provider.

## Configure

Copy `.env.example` to `.env` and set at least one provider:

```
ANTHROPIC_API_KEY=sk-ant-...        # default provider
GROQ_API_KEY=gsk_...                # free tier, with --provider groq
AZURE_OPENAI_API_KEY=...            # with AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT, --provider azure
GITHUB_TOKEN=ghp_...                # optional, raises the GitHub API rate limit
```

## Run locally (without Docker)

You need Python 3.9+, Git, and Go 1.21+ (or Docker, which is used automatically to run tests).

```bash
pip install -r requirements.txt
python solve.py implement --issue https://github.com/spf13/cobra/issues/2396
```

## Output

| Mode | File(s) in `output/` |
|------|------|
| `explain` | `issue-<N>/explanation.md` |
| `implement` | `issue-<N>/pr.md` (title, description, diff), plus `changes.patch` and `result.json` |
| `review` | `pr-<N>/review.md` |

Two flags worth knowing. `--base-commit <sha>` solves the issue at the state before the fix
landed, which is how you evaluate fairly against an already-merged PR. `--guidance "..."` steers
`implement` toward a preferred fix.

## Demos and evaluation

The reproduction-test oracle is the part worth seeing. Two captured runs show it: the agent
writes a test that fails on the bug, fixes the code, and the test passes (verified, not
assumed). See [`sample_outputs/validator_issue_1576.md`](sample_outputs/validator_issue_1576.md)
and [`sample_outputs/validator_issue_1550.md`](sample_outputs/validator_issue_1550.md).

For numbers, [`eval/SWE_RESULTS.md`](eval/SWE_RESULTS.md) reports 27 held-out cobra issues
solved at their pre-fix commit and compared against the real merged PRs. File localization went
from 78% to 93% after fixes the evaluation itself surfaced. Reproduce it with
`python -m eval.swe_eval`.

## Supported repositories

Convention rules ship for `spf13/cobra`, `gin-gonic/gin`, `go-playground/validator`, and
`golangci/golangci-lint`. Pre-built indexes ship for cobra and validator. For any other Go repo,
build one with `python build_index.py --repo <owner/name>`. The agent works on any Go
repository; the rules and indexes just improve quality on the ones above.

## Limitations

- Go only. The tools, rules, and parsing are specific to Go.
- It runs the target repo's `go test`, which is untrusted code. The Docker path isolates it.
- On very large repos the repo map is size-capped, so localization there leans more on
  retrieval than on the map.

For the pipeline design, the reproduction oracle, retrieval and indexing, and how this compares
to Agentless, SWE-agent, AutoCodeRover, and Aider, see [ARCHITECTURE.md](ARCHITECTURE.md).
