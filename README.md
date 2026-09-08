# go-issue-agent

Give it a GitHub issue from a Go repository. It finds the code, writes a test that reproduces the
bug, patches the source, runs `go build`, `go test`, and `go vet`, and writes a pull request
title, description, and diff to a local file. It never pushes or opens a PR.

Three modes, one CLI (`solve.py`):

| Mode | What it does | Output |
|------|--------------|--------|
| `explain` | Explain the issue and propose a fix. No code changes. | `output/issue-<N>/explanation.md` |
| `implement` | Localize, plan, reproduce, patch, validate, write the PR text. | `output/issue-<N>/pr.md`, `changes.patch`, `pr_summary.md`, `result.json` |
| `review` | Review an existing PR against its linked issue and the project's conventions. | `output/pr-<N>/review.md` |

Built on LangGraph and LangChain. Works with Anthropic (default), Groq, or Azure OpenAI.

## Why

Most coding agents are one long free-form loop; when they go wrong you cannot tell which step
failed. This one is a fixed LangGraph `StateGraph` (`agent/pipeline.py`) where only the patch
and heal steps are open-ended tool loops, and both have a step budget.

Most agents also declare victory when the existing tests still pass. This one writes a
reproduction test first, checks that it compiles and fails on the unfixed code, and uses it to
pick the winning patch. "Matched the right file" becomes "fixed it, verified by a test."

It is Go-specific on purpose: a symbol index from Go declarations, `go` and `gofmt` as the only
build tools, and per-repo convention files in `rules/`.

## Demo

A captured `implement` run on go-playground/validator #1576, at the pre-fix commit
(from [`sample_outputs/validator_issue_1576.md`](sample_outputs/validator_issue_1576.md)):

```
[reproduce] Generating a fail→pass reproduction test...
  ✓ Repro test TestCronValidationRejectsEmbeddedCronSubstring FAILS on unfixed code
    (valid fail→pass oracle, attempt 1).

[phase 4/5] Generating 3 patch candidates in parallel...
  Candidate 1: DONE  tests=FAIL  edit=src  repro=PASS
  Candidate 2: DONE  tests=FAIL  edit=src  repro=PASS
  Candidate 3: DONE  tests=PASS  edit=src  repro=PASS
  Best candidate: #3 (repro PASS, tests pass, source edit, diff 2271)

  Reproduction test TestCronValidationRejectsEmbeddedCronSubstring: PASS, issue fixed ✓
  Build: PASS
  Tests: PASS
  Vet:   PASS
```

All three candidates made the reproduction test pass, but two broke the existing suite. The
ranking picked the one that passed both.

See also [`validator_issue_1550.md`](sample_outputs/validator_issue_1550.md) and the annotated
[`cobra_issue_2396.md`](sample_outputs/cobra_issue_2396.md).

<!-- TODO: terminal gif of `solve.py implement` on a cobra issue, end to end -->

## Quickstart

### 1. Configure a provider

```bash
cp .env.example .env
# set one of: ANTHROPIC_API_KEY (default) | GROQ_API_KEY (--provider groq)
#             AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_DEPLOYMENT (--provider azure)
# optional:   GITHUB_TOKEN for a higher GitHub API rate limit
```

`solve.py` reads `.env` from the repo directory on its own. Docker needs `--env-file .env`.

### 2a. Run with Docker

The image is based on `golang:1.25-bookworm`, so `go test` runs natively inside the container.
It bundles the Python deps, prompts, rules, and the pre-built indexes for cobra and validator.

```bash
docker build -t go-issue-agent .

# A bare issue defaults to `implement`. Writes output/issue-2396/pr.md on the host.
docker run --rm --env-file .env -v "$PWD/output:/app/output" \
  go-issue-agent https://github.com/spf13/cobra/issues/2396
```

Explicit modes and provider switch:

```bash
docker run --rm --env-file .env -v "$PWD/output:/app/output" go-issue-agent explain   --issue 2396 --repo spf13/cobra
docker run --rm --env-file .env -v "$PWD/output:/app/output" go-issue-agent implement --issue 2396 --repo spf13/cobra --provider groq
docker run --rm --env-file .env -v "$PWD/output:/app/output" go-issue-agent review    --pr   2356 --repo spf13/cobra
```

### 2b. Run locally

You need Python 3.9+, Git, and either a Go toolchain or a reachable Docker daemon. If `go` is
not on your machine, Go commands run inside the official `golang:<version>` image, with the
version taken from the target repo's `go.mod`.

```bash
pip install -r requirements.txt
python solve.py implement --issue https://github.com/spf13/cobra/issues/2396
```

### 3. Check the result

```bash
cat output/issue-2396/pr.md        # PR title + description + validation line + diff
git -C workspace/cobra diff        # the same change, applied on local branch fix/issue-2396
```

Two flags worth knowing: `--base-commit <sha>` solves the issue at the state before the real fix
landed, for fair comparison against a merged PR. `--guidance "..."` steers `implement` toward a
preferred fix.

## How it works

`build_graph(mode)` in `agent/pipeline.py` wires one of three graphs from the same nodes. This is
the `implement` graph:

```
                     issue URL or number
                            |
                            v
          setup      clone the repo, reset a local branch fix/issue-N
                     (or --base-commit), load indexes/<repo>/ or build a
                     symbol index on the fly, build the repo map
                            |
                            v
 LOCATE   retrieve   BM25 over symbols.json (top 25) and pr_examples.json (top 3)
          localize   one structured LLM call -> edit / new-code / test locations
                            |
                            v
 PLAN     plan       root cause + fix steps, using rules/<repo>.md and --guidance
          reproduce  LLM writes a Go test that must compile and FAIL on the
                     unfixed code; repaired from `go test` output, up to 3 tries
                            |
                            |  Send fan-out: one git worktree per candidate (default 3)
                            v
 EDIT     +-------------+ +-------------+ +-------------+
          | candidate 1 | | candidate 2 | | candidate 3 |  ReAct tool loop, up to 60
          | minimal     | | edge cases  | | copy style  |  steps each, one retry if
          +-------------+ +-------------+ +-------------+  the diff is test-only
                 |               |               |
                 +---------------+---------------+
                                 v
          select_patch  rank by (repro_pass, tests_pass, source edit, smallest diff)
                        and git apply the winner onto the main clone
                                 |
                                 v
 TEST     validate      go build ./...   go test ./...   go vet ./...
                                 |
                   tests fail    |    pass, or no Go, or 3 rounds used
                 +---------------+----------------------------+
                 v                                            |
 RETRY    self_heal     ReAct loop on the failing output,     |
                        up to 80 steps, rolled back on crash  |
                 |                                            |
                 +----> back to validate                      |
                                                              v
          summary       LLM writes the PR title + body -> output/issue-N/pr.md
```

1. `setup` (`agent/github_client.py`) clones or fetches the repo into `workspace/`, then runs
   `checkout --force`, `reset --hard`, `clean -fd`, `checkout -B fix/issue-N`. The index comes
   from `indexes/<owner>_<repo>/` if present, else `agent/indexer.py` builds symbols on the fly.
2. `retrieve` (`agent/retrieval.py`) is BM25 in pure Python over the symbol index and the
   merged-PR examples. No embeddings, no vector store.
3. `localize` sends `prompts/localize_deep.md` through `with_structured_output(Localization)`.
   If that raises, it falls back to `prompts/localize.md` and a JSON parse.
4. `plan` writes the fix strategy. `reproduce` asks for a `ReproTest`, writes it into a
   throwaway worktree, and runs `go test -run <name>`. A test that passes or fails to compile
   is sent back with the output for repair. After 3 failures the oracle is skipped.
5. `patch_candidate` runs N `create_agent` ReAct loops in parallel via LangGraph `Send`, each in
   its own `git worktree` (`agent/go_utils.py:Worktree`) with the failing test pre-written.
   After the loop the canonical test is restored (so the agent cannot weaken it) and re-run.
6. `select_patch` ranks candidates and applies the winner with `git apply`, falling back to
   `--3way`.
7. `validate` and `self_heal` loop until tests pass, Go is unavailable, or 3 heal rounds are
   used. A heal round that crashes is rolled back to the pre-round diff.
8. `summary` renders `prompts/validate.md` into the PR text and writes the output files.

`explain` stops after `plan` and writes `prompts/explain.md`. `review` checks out the PR's base
SHA, loads up to 8 changed Go files, fetches the linked issue, and runs `prompts/review.md`.

### LLM providers

`agent/llm_client.py:make_chat_model` returns a LangChain chat model:

| `--provider` | Class | Default model |
|--------------|-------|---------------|
| `anthropic` | `ChatAnthropic` | `claude-sonnet-4-6` |
| `groq` | `ChatGroq` | `llama-3.3-70b-versatile` |
| `azure` | `AzureChatOpenAI` | `AZURE_OPENAI_DEPLOYMENT`, else `gpt-4o` |

All use `max_tokens=4096` and `max_retries=6`. Azure sends `max_completion_tokens`, so o-series
deployments work.

### Agent tools

`make_tools()` gives the ReAct loops seven tools bound to one worktree: `list_directory`,
`read_file`, `search_code` (grep over `*.go`), `edit_file` (exact, unique match), `replace_lines`
(line range, for big string literals), `create_file`, `run_command`. Every tool returns an error
string instead of raising, so a bad call never kills the loop.

### Safety boundaries

- No network writes. GitHub is only ever read (`GET` issues, PRs, PR files). There is no
  `git push`, PR creation, or comment posting anywhere in the repo.
- `run_command` accepts only `go`, `git`, `gofmt`, `golangci-lint` as the first token and runs
  them without a shell (`tools.py`). The check is on the binary name only, so `git` is allowed
  with any subcommand. The clone is unauthenticated `https://` and no prompt asks for a push.
- Every write lands in `workspace/` (a local branch of the clone), a temp worktree (removed when
  the candidate finishes), or `output/`.
- `go test` of the target repo is untrusted code. With a local `go` it runs on your machine; the
  Docker image runs it inside the container. Commands time out (120 s default, 300 s for tests).

## Features

| Feature | Where |
|---------|-------|
| Three modes: `explain`, `implement`, `review` | `solve.py`, `build_graph(mode)` |
| Reproduction-test oracle with compile/fail verification and repair | `pipeline.py:reproduce` |
| N parallel patch candidates in separate git worktrees, ranked | `route_to_candidates`, `select_patch` |
| Validate + self-heal loop, 3 rounds, rollback on crash | `validate`, `self_heal` |
| BM25 retrieval over symbols and merged PRs, pure Python | `agent/retrieval.py` |
| Offline index builder: Go symbols + recent merged PRs | `build_index.py`, `agent/indexer.py` |
| Go via local binary or the `golang:<go.mod version>` Docker image | `agent/go_utils.py` |
| `--base-commit` for pre-fix evaluation, `--guidance` to steer | `solve.py`, `create_fix_branch` |
| Per-repo convention rules for cobra, gin, validator, golangci-lint | `rules/*.md` |
| Pre-built indexes for `spf13/cobra` and `go-playground/validator` | `indexes/` |
| SWE-bench-style eval harness and offline localization-recall eval | `eval/` |

## Configuration

Environment variables (`.env` is loaded by `solve.py`, `build_index.py`, and `eval/swe_eval.py`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | none | Required for `--provider anthropic` (the default). |
| `GROQ_API_KEY` | none | Required for `--provider groq`. |
| `AZURE_OPENAI_API_KEY` | none | Required for `--provider azure`. |
| `AZURE_OPENAI_ENDPOINT` | none | Required for `--provider azure`. |
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-4o` | Azure deployment name. Doubles as the Azure model default. |
| `AZURE_OPENAI_API_VERSION` | `2024-02-01` | Azure API version. |
| `GITHUB_TOKEN` | none | Optional. Higher API rate limit for issue/PR fetch and index building. |
| `GIA_NUM_CANDIDATES` | `3` | Patch candidates to sample in parallel. |
| `GIA_SKIP_TESTS` | unset | Any value except `0`/`false` skips `go build/test/vet`, the reproduction oracle, and self-heal. |

CLI flags (`python solve.py <mode> --help`):

| Flag | Modes | Default | Purpose |
|------|-------|---------|---------|
| `--issue` | explain, implement | required | Issue URL or bare number. |
| `--pr` | review | required | PR URL or bare number. |
| `--repo` | all | `spf13/cobra` | `owner/name` used when a bare number is given. |
| `--provider` | all | `anthropic` | `anthropic`, `groq`, or `azure`. |
| `--model` | all | per provider | Model name, or the Azure deployment name. |
| `--base-commit` | explain, implement | none | Solve at this commit instead of the default branch tip. |
| `--guidance` | implement | none | Free text injected into the plan prompt. |
| `--workspace` | all | `./workspace` | Where repos are cloned. |
| `--output` | all | `./output` | Where results are written. |

## Design decisions

- **Fixed phases, bounded loops.** Only `patch_candidate` and `self_heal` are open-ended
  `create_agent` loops, and both carry a `recursion_limit`. Less autonomy, but a run you can
  read top to bottom.
- **The oracle abstains rather than lies.** If no reproduction test compiles and fails within 3
  attempts, `repro_valid` is `False` and selection falls back to `(tests_pass, source edit,
  smallest diff)`. The oracle needs Go, so `GIA_SKIP_TESTS` batch runs never use it.
- **Worktrees plus text diffs, not shared state.** Candidates never touch the main clone. The
  winning diff is re-applied with `git apply`, then `--3way` on failure. `go.mod`/`go.sum` are
  reverted after every test run because the Go toolchain writes a `toolchain` line.
- **BM25 in pure Python.** 650 symbols for cobra and 901 for validator do not need an embedding
  service. The retrieval layer is 135 lines and runs offline.
- **The workspace clone is disposable.** `create_fix_branch` hard-resets and cleans it every run
  so diffs are reproducible. Do not keep work there.
- **Docker for Go, not for the agent.** Locally only `go` commands are containerized (worktree
  mounted at `/app`). Inside the image Go is native, so there is no Docker-in-Docker.

## Project layout

```
solve.py              CLI: explain / implement / review (click)
build_index.py        Offline index builder: symbols.json + pr_examples.json
docker-entrypoint.sh  Bare issue -> `solve.py implement`; modes pass through
agent/
  pipeline.py         LangGraph StateGraph, all nodes, wired per mode
  llm_client.py       make_chat_model(): Anthropic / Groq / Azure OpenAI
  tools.py            ToolExecutor + make_tools(): the agent's 7 tools
  github_client.py    Issue/PR fetch, clone, fix-branch reset, HTML fallback
  indexer.py          Go symbol extraction, merged-PR examples, save/load
  retrieval.py        BM25 over symbols and PR examples
  repomap.py          Directory tree + top-level declarations for the prompt
  go_utils.py         find go/docker, go build/test/vet, Worktree, get_diff
prompts/              One Markdown prompt per node (system, localize_deep, plan, ...)
rules/                Convention rules: cobra, gin, validator, golangci-lint
indexes/              Pre-built indexes: spf13_cobra, go-playground_validator
eval/                 Localization-recall eval, SWE-style eval, datasets, results
sample_outputs/       Captured and annotated runs
```

## Development

There are no unit tests and no CI workflow. Lint and the two eval harnesses are what exists.

```bash
# Lint (config in ruff.toml; ruff is not in requirements.txt)
pip install ruff && ruff check .

# Offline localization-recall eval over the shipped cobra index. No API key.
python -m eval.localization_eval
python -m eval.localization_eval --top-k 15

# SWE-style eval: 27 cobra issues at their pre-fix commit, diff vs the gold PR.
# Defaults: --provider azure, 1 candidate, Go tests skipped. Resumes from eval/runs/.
python -m eval.swe_eval --provider azure --limit 5

# Rebuild the SWE dataset from git history (workspace/cobra must exist)
python -m eval.build_swe_dataset --repo spf13/cobra --n 30

# Index another Go repo (set GITHUB_TOKEN for the PR fetch)
python build_index.py --repo gin-gonic/gin
```

Numbers from [`eval/SWE_RESULTS.md`](eval/SWE_RESULTS.md) (27 cases, 1 candidate, tests skipped):
fix produced 100%, right file localized 93%, exact file set 67%, recall 0.809, precision 0.901.

## Limitations

- Go only. Symbol extraction, tools, and rules assume Go.
- No guard for non-code issues. A docs-only issue still enters the patch loop and tries to make
  a Go change (flagged by `eval/localization_eval.py`).
- The validator index ships without PR examples (`pr_examples.json` is empty), so style
  retrieval only helps on cobra. Build your own with `build_index.py`.
- Both evals cover cobra only (7 and 27 cases).
- Prompt inputs are capped: 8 located files, 12,000 chars of review diff and file context, 60
  files in the repo map. On big repos localization leans on BM25 more than the map.
- `run_command` allows `git` wholesale. Nothing blocks a `git push` by name; the protection is
  that the clone has no credentials and no prompt asks for it.

## Contributing

New repo rules go in `rules/<name>.md`; new tools are a `_tool_<name>` on `ToolExecutor` exposed
in `make_tools()`; eval cases go in `eval/dataset.json`. Run `ruff check .` before opening a PR.

## License

No license file yet.
