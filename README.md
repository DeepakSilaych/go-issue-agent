# go-issue-agent

An agentic assistant for GitHub issues in open-source **Go** projects. Give it an issue; it
finds the relevant code, fixes it, runs the tests, and writes a pull request (title +
description + diff).

Three modes:

- **`explain`** — explain the issue and propose a fix (no code changes)
- **`implement`** — localize → patch → validate → emit a PR (title + description + diff)
- **`review`** — review a pull request against its issue and the project's conventions

Built on LangGraph + LangChain. See **[ARCHITECTURE.md](ARCHITECTURE.md)** for how it works.

## Quick start (Docker)

The image bundles Python, the Go toolchain, and pre-built indexes (cobra + validator), so one
command takes an issue and writes a Markdown file with the PR title, description, and diff.

```bash
docker build -t go-issue-agent .

# Issue (URL or number) → output/issue-<N>/pr.md
docker run --rm --env-file .env -v "$PWD/output:/app/output" \
  go-issue-agent https://github.com/spf13/cobra/issues/2396
```

All three modes:

```bash
docker run --rm --env-file .env -v "$PWD/output:/app/output" go-issue-agent explain   --issue 2396 --repo spf13/cobra
docker run --rm --env-file .env -v "$PWD/output:/app/output" go-issue-agent implement --issue 2396 --repo spf13/cobra
docker run --rm --env-file .env -v "$PWD/output:/app/output" go-issue-agent review    --pr   2356 --repo spf13/cobra
```

A bare issue defaults to `implement`. Add `--provider groq|azure` for a different provider.

## Configure

Copy `.env.example` to `.env` and set at least one provider:

```
ANTHROPIC_API_KEY=sk-ant-...        # default provider
GROQ_API_KEY=gsk_...                # free tier  (--provider groq)
AZURE_OPENAI_API_KEY=...            # + AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT (--provider azure)
GITHUB_TOKEN=ghp_...                # optional, raises the GitHub API rate limit
```

## Run locally (without Docker)

Needs Python 3.9+, Git, and Go 1.21+ (or Docker, used automatically to run tests).

```bash
pip install -r requirements.txt
python solve.py implement --issue https://github.com/spf13/cobra/issues/2396
```

## Output

| Mode | File(s) in `output/` |
|------|------|
| `explain` | `issue-<N>/explanation.md` |
| `implement` | `issue-<N>/pr.md` (title + description + diff), plus `changes.patch`, `result.json` |
| `review` | `pr-<N>/review.md` |

Useful flags: `--base-commit <sha>` (solve at the state *before* the fix landed — for fair,
SWE-bench-style evaluation) and `--guidance "..."` (steer `implement` toward a preferred fix).

## Demos & evaluation

- **Reproduction-test oracle** (real captured runs):
  [`sample_outputs/validator_issue_1576.md`](sample_outputs/validator_issue_1576.md),
  [`sample_outputs/validator_issue_1550.md`](sample_outputs/validator_issue_1550.md) — the agent
  writes a test that fails on the bug, fixes it, and the test passes (verified, not assumed).
- **Evaluation**: [`eval/SWE_RESULTS.md`](eval/SWE_RESULTS.md) — 27 held-out cobra issues solved
  at their pre-fix commit and compared to the real merged PRs (localized **78% → 93%** after
  fixes the eval itself surfaced). Reproduce with `python -m eval.swe_eval`.

## Supported repositories

Convention rules ship for `spf13/cobra`, `gin-gonic/gin`, `go-playground/validator`, and
`golangci/golangci-lint`; pre-built indexes ship for cobra and validator. Build an index for
any other Go repo with `python build_index.py --repo <owner/name>`. Works on any Go repository.

## Limitations

- **Go only** — tools, rules, and parsing are Go-specific.
- **Runs untrusted code** — executes the target repo's `go test`; the Docker path isolates it.
- **Large repos** — the repo map is size-capped, so localization leans more on retrieval there.

---

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the pipeline design, the reproduction oracle,
retrieval/indexing, and how it compares to Agentless / SWE-agent / AutoCodeRover / Aider.
