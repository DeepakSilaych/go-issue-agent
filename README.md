# go-issue-agent

An agentic AI system that assists with GitHub issues from open-source Go projects. It has **three modes**:

- **`explain`** — explain the issue and the proposed solution (analysis, no code changes)
- **`implement`** — localize, plan, patch, validate, and produce a diff + PR summary
- **`review`** — review a pull request against its issue and the project's conventions

In `implement` mode the agent clones the repo, retrieves relevant symbols and similar past PRs, localizes the fix down to exact lines, plans a targeted change, generates **multiple patch candidates in parallel** and ranks them by test results, self-heals failing tests, and produces a diff and a pull-request summary.

---

## Architecture

The system is a **5-phase pipeline** built as a **[LangGraph](https://langchain-ai.github.io/langgraph/) `StateGraph`** (`agent/pipeline.py`). Each phase is a graph node with a specific, bounded goal — there is no free-form autonomous loop except inside the patch and self-heal steps, where it runs as a `create_agent` ReAct loop with a tight step budget. The LLM layer is **LangChain** chat models (`langchain-anthropic` / `langchain-groq` / `langchain-openai`), so provider differences are handled by the framework rather than custom glue.

The patch candidates are a **`Send`-based fan-out** (map/reduce): `plan` dispatches one `patch_candidate` per strategy, each runs in its own git worktree, and results are reduced in `select_patch`. Validation feeds a conditional **self-heal loop** (`validate ⇄ self_heal`) until tests pass or the round budget is exhausted.

```
Issue URL
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 1: RETRIEVE                                          │
│  Load (or build) an offline index of the repo.             │
│  BM25-rank Go symbols against the issue text.              │
│  BM25-rank similar past merged PRs (style + file hints).   │
│  → top-k symbols, top-k similar PRs                         │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 2: LOCALIZE  (hierarchical: file → func → line)     │
│  Given retrieved symbols + a repo map, ask the model for    │
│  precise edit / new-code / test locations.                  │
│  → edit_locations, new_locations, test_locations, context   │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 3: PLAN                                             │
│  Load located file contents + similar-PR context.          │
│  → root cause, step-by-step fix strategy, validation cmd    │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 4: MULTI-PATCH  (3 candidates in parallel)          │
│  Each candidate runs in an isolated git worktree with a    │
│  different strategy (minimal / coverage / pattern-match),   │
│  using a tool-use loop:                                     │
│    read_file, search_code, edit_file,                       │
│    create_file, run_command (go build/test/vet)            │
│  Each candidate is tested; the best is selected             │
│  (tests pass first, then smallest diff).                    │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 5: VALIDATE + SELF-HEAL                             │
│  Apply the winning diff to the working tree.               │
│  Run go build / test / vet. If tests fail, feed failures    │
│  back to the model for up to 3 heal rounds.                 │
│  Generate the PR title + body from diff + test results.     │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
              output/issue-{N}/
                pr_summary.md
                changes.patch
                result.json
```

If no local `go` binary is found, validation runs inside the official `golang` Docker
image (tag derived from the target repo's `go.mod`), so the agent works without a Go
toolchain installed.

### Key Design Choices

| Choice | Reason |
|--------|--------|
| LangGraph `StateGraph` | Phases/edges/loops are explicit and inspectable; the heal loop and candidate fan-out are first-class graph constructs, not ad-hoc control flow |
| LangChain chat models | Provider differences (Anthropic/Groq/Azure tool-calling, structured output) handled by the framework instead of custom normalization code |
| Fixed 5-phase pipeline | Reliable, interpretable, easy to debug vs. a free-form agent |
| BM25 retrieval (pure Python) | Lightweight RAG with no embedding service or heavy deps |
| Similar-PR retrieval | Grounds the fix in the project's real conventions and file layout |
| Hierarchical localization | Narrows to exact functions/lines, not just "relevant files" |
| Multi-candidate + test ranking | Agentless-style sampling; picks the candidate that actually passes tests |
| Git worktrees per candidate | Candidates are generated in true isolation, in parallel |
| Self-heal loop | Feeds test failures back to the model instead of giving up |
| Project-specific rules (`rules/*.md`) | Encodes per-project Go conventions so output matches project style |
| `edit_file` requires exact match | Prevents hallucinated edits; forces read-before-edit |
| Only `go`/`git`/`gofmt`/`golangci-lint` commands allowed | Sandboxes side effects to safe operations |

---

## Supported Repositories

Ships with project-specific convention rules (`rules/*.md`) for the four approved repos:

- `spf13/cobra` — CLI framework
- `gin-gonic/gin` — HTTP framework
- `go-playground/validator` — struct validation
- `golangci/golangci-lint` — linter aggregator

Works on any Go repository; the rules files improve output quality for the above.
A **pre-built index for `spf13/cobra` ships in `indexes/spf13_cobra/`**, so the agent's
symbol- and PR-retrieval work out of the box for cobra without any setup. For the other
repos, build an index first (see below).

---

## Setup

### Requirements

- Python 3.9+
- Git
- Go 1.21+ **or** Docker (for running tests in the target repo; Docker is used automatically if `go` is absent)
- An LLM API key — Anthropic, Groq (free tier), or Azure OpenAI

### Install

```bash
git clone https://github.com/DeepakSilaych/go-issue-agent
cd go-issue-agent
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# Edit .env — add at least one of ANTHROPIC_API_KEY / GROQ_API_KEY / AZURE_OPENAI_*
```

`.env`:
```
# Option A: Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Option B: Groq (free tier) — https://console.groq.com
GROQ_API_KEY=gsk_...

# Option C: Azure OpenAI — set endpoint + deployment too (see .env.example)
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=gpt-4o

# Optional: raises GitHub API rate limit from 60 to 5000 req/hr (used by build_index.py)
GITHUB_TOKEN=ghp_...
```

---

## Building an Index (Phase 1 knowledge)

`build_index.py` builds the offline knowledge index a repo's Retrieve phase uses:

- **Symbol index** — every top-level `func`/`type`/`const`/`var` with file, line, and doc comment
- **PR examples** — recent merged PRs (linked issue + changed-files summary) for style/conventions
- **Test helpers** — test utility functions available in the repo

```bash
python build_index.py --repo spf13/cobra        # already shipped pre-built
python build_index.py --repo gin-gonic/gin
python build_index.py --repo go-playground/validator
python build_index.py --repo golangci/golangci-lint
```

> Set `GITHUB_TOKEN` before building, or the PR-example fetch will be rate-limited
> (unauthenticated GitHub allows ~60 requests/hour). Symbol and test-helper indexing
> are fully offline and need no token.

If you run `solve.py` on a repo with **no** pre-built index, the agent transparently
builds a symbol-only index on the fly (no PR examples) so it still runs — just without
the PR-style grounding.

---

## Usage — three modes

The CLI has three subcommands. They share the LangGraph nodes; each mode wires a different
sub-path of the graph.

```bash
# 1) EXPLAIN — explain the issue and the proposed solution (no code changes)
#    graph: setup → retrieve → localize → plan → explain
python solve.py explain --issue https://github.com/spf13/cobra/issues/2396
#    → output/issue-2396/explanation.md

# 2) IMPLEMENT — localize, plan, patch (×3 candidates), validate, write a PR summary
#    graph: setup → retrieve → localize → plan → patch ×N → select → validate ⇄ heal → summary
python solve.py implement --issue https://github.com/spf13/cobra/issues/2396
#    steer toward a preferred/modified solution:
python solve.py implement --issue 2396 --guidance "use a three-index slice, not a copy"
#    solve at the pre-fix commit (fair, SWE-bench-style evaluation):
python solve.py implement --issue 2396 --base-commit <sha>
#    → output/issue-2396/{pr_summary.md, changes.patch, result.json}

# 3) REVIEW — review a pull request against its issue + project conventions
#    graph: setup_review → review
python solve.py review --pr https://github.com/spf13/cobra/pull/2356
#    → output/pr-2356/review.md
```

Provider/output flags work on every subcommand:

```bash
python solve.py implement --issue 2396 --provider groq
python solve.py explain   --issue 2396 --provider azure
python solve.py review    --pr 2356   --workspace ./repos --output ./results
```

### Options (shared)

```
--provider   LLM provider: anthropic | groq | azure (default: anthropic)
--model      Model name / Azure deployment (provider-specific default)
--workspace  Directory to clone repos into (default: ./workspace)
--output     Directory for output artifacts (default: ./output)

explain / implement:  --issue <url|number>  [--repo owner/name]  [--base-commit <sha>]
implement only:       --guidance "<preferred or modified solution>"
review:               --pr <url|number>     [--repo owner/name]
```

### Provider notes

- **Anthropic** (default): `claude-sonnet-4-6`. Pass `--model claude-opus-4-8` for harder issues.
- **Groq** (free tier): supports tool calling on `llama-3.3-70b-versatile` (128k context).
  Rate limits (~30 req/min) can slow the patch loop; the client retries on 429 with backoff.
- **Azure OpenAI**: requires `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_DEPLOYMENT` (the
  `--model` value or `AZURE_OPENAI_DEPLOYMENT` is your deployment name).

---

## Output

Each mode writes to `output/`:

| Mode | Path | Files |
|------|------|-------|
| `explain` | `output/issue-{N}/` | `explanation.md` — issue + root cause + proposed solution + validation plan |
| `implement` | `output/issue-{N}/` | `pr_summary.md` (PR title/body), `changes.patch` (`git diff`), `result.json` (diff, `tests_pass`, `build_ok`, `vet_ok`, `diff_applied`, test output, winning candidate id) |
| `review` | `output/pr-{N}/` | `review.md` — summary, addresses-issue, correctness, conventions, tests, suggestions, verdict |

In `implement` mode the modified repo is left on a branch named `fix/issue-{N}` in `workspace/{repo-name}/`.
Re-running on the same issue resets that branch to the upstream default branch first, so
diffs are reproducible rather than cumulative.

---

## Sample Output

See [`sample_outputs/cobra_issue_2396.md`](sample_outputs/cobra_issue_2396.md) for an
annotated walkthrough of the agent solving cobra#2396 (add a `NoDuplicateArgs` validator),
showing each of the five phases and the resulting diff and PR summary.

---

## Project Structure

```
go-issue-agent/
├── solve.py              # CLI entry point
├── build_index.py        # Offline index builder (symbols + PRs + test helpers)
├── requirements.txt
├── .env.example
│
├── agent/
│   ├── pipeline.py       # LangGraph StateGraph: the 5-phase pipeline
│   ├── llm_client.py     # make_chat_model() → LangChain chat model (Anthropic/Groq/Azure)
│   ├── tools.py          # ToolExecutor + make_tools() LangChain @tool wrappers (the ACI)
│   ├── github_client.py  # Issue fetching, repo cloning, branch management
│   ├── indexer.py        # Symbol / PR / test-helper index construction
│   ├── retrieval.py      # BM25 retrieval over the index
│   ├── repomap.py        # Lightweight repo map for localization
│   └── go_utils.py       # Go toolchain (local or Docker) + git worktrees
│
├── prompts/              # Phase-specific prompts (loaded at runtime)
│   ├── system.md
│   ├── localize_deep.md  # primary localization prompt
│   ├── localize.md       # fallback localization prompt
│   ├── plan.md
│   ├── patch_candidate.md
│   ├── patch.md
│   └── validate.md
│
├── rules/                # Project-specific conventions
│   ├── cobra.md
│   ├── gin.md
│   ├── validator.md
│   └── golangci-lint.md
│
├── indexes/
│   └── spf13_cobra/      # Pre-built index shipped for cobra
│
└── sample_outputs/
    └── cobra_issue_2396.md
```

---

## How It Compares to Other Systems

The architecture borrows deliberately:

| System | Inspiration taken |
|--------|------------------|
| [Agentless](https://github.com/OpenAutoCoder/Agentless) | Fixed phases + sampling multiple patches and ranking by tests |
| [SWE-agent](https://github.com/SWE-agent/SWE-agent) | ACI-style tools built for LLMs (not raw bash), with informative errors |
| [AutoCodeRover](https://github.com/AutoCodeRoverSG/auto-code-rover) | Structure-first localization over an AST-aware symbol index |
| [Aider](https://aider.chat) | Repo map concept; project conventions as explicit rules |

The bet: a focused, readable pipeline that evaluators can understand and extend beats a
feature-complete but opaque autonomous agent.

---

## Extending the System

### Add rules for a new repository

Create `rules/{repo-name}.md` following `rules/cobra.md`: package structure, error handling,
test style, naming, and what to avoid.

### Add a new tool

1. Add a `_tool_{name}` method to `ToolExecutor` in `agent/tools.py`.
2. Expose it as a `@tool` inside `make_tools()` (it's automatically given to the patch
   and self-heal agents).
3. Document it in `prompts/patch_candidate.md`.

### Change the model

Pass `--model claude-opus-4-8` for higher quality on complex issues, or
`claude-haiku-4-5-20251001` for speed.

---

## Limitations

- **Sequential phases**: an error in an early phase (e.g. localization) cascades. Each phase
  has a fallback, but there is no global retry across phases.
- **Repo map is size-capped**: very large repos (e.g. golangci-lint) have their repo map
  truncated; localization there leans more on BM25 retrieval than the map.
- **Runs untrusted code**: `go test` executes the target repo's test code. Prefer the Docker
  path for isolation when running against unfamiliar repos.
- **No browser/web access**: issues that link to external docs are handled from the model's
  training knowledge only.
- **Go only**: tool restrictions and rules are Go-specific.
