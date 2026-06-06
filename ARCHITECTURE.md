# Architecture

`go-issue-agent` is a **[LangGraph](https://langchain-ai.github.io/langgraph/) `StateGraph`**
(`agent/pipeline.py`). Each phase is a graph node with a single bounded job; the only
open-ended loops are the patch and self-heal steps, which run as LangChain `create_agent`
(ReAct) loops with a tight step budget. The LLM layer is LangChain chat models
(`langchain-anthropic` / `langchain-groq` / `langchain-openai`), so provider differences are
handled by the framework, not custom glue.

`build_graph(mode)` wires one of three sub-graphs from the same node set.

## The `implement` graph

```
START
  ▼
setup ── retrieve ── localize ── plan ── reproduce
                                             │
                            (Send fan-out, 1 per candidate)
                                             ▼
                                     patch_candidate ×N   (each in its own git worktree,
                                             │             driven by create_agent / TDD)
                                             ▼
                                       select_patch  (rank: repro_pass, tests,
                                             │         source-edit, smallest diff)
                                             ▼
                                         validate  ◀────────┐
                                             │              │
                                  (tests fail & go avail)   │
                                             ▼              │
                                         self_heal ─────────┘
                                             │
                                    (pass / no go / out of rounds)
                                             ▼
                                          summary ── END
```

- **`explain`**: `setup → retrieve → localize → plan → explain → END`
- **`review`**: `setup_review → review → END` (input is a `PullRequest`, not an `Issue`)

## Phases

1. **Retrieve** (`retrieval.py`) — pure-Python **BM25** over the repo's symbol index ranks
   relevant Go symbols against the issue text, and ranks similar past merged PRs (style + file
   hints). No embeddings, no vector store.
2. **Localize** (`prompts/localize_deep.md`) — one structured call
   (`with_structured_output`) returns precise edit / new-code / test locations
   (file → function → line). Falls back to a text prompt + JSON parse if structured output
   fails.
3. **Plan** (`prompts/plan.md`) — root cause + step-by-step fix strategy, grounded in the
   located files, similar PRs, and the repo's convention rules. `--guidance` is injected here.
4. **Reproduce** (`prompts/reproduce.md`) — the oracle (see below).
5. **Multi-Patch** — a **`Send` fan-out**: `N` candidates (default 3), each in an isolated
   **git worktree** with a different strategy (minimal / coverage / pattern-match), driven by a
   `create_agent` tool loop. `select_patch` ranks them by
   `(repro_pass, tests_pass, has_source_edit, smallest_diff)` and applies the winner.
6. **Validate + Self-heal** — runs `go build` / `test` / `vet`; on failure, a conditional
   `validate ⇄ self_heal` loop feeds the failures back to the agent (≤3 rounds; crashed rounds
   roll back so they can't pollute the diff).
7. **Summary** — writes the PR title/body and the combined `pr.md` (title + description + diff).

If no local `go` binary is found, the Go steps run inside the official `golang` Docker image
(tag derived from the target repo's `go.mod`), so it works without a local toolchain.

## The reproduction-test oracle

Before patching, `reproduce` asks the model for a Go test that asserts the *correct* behaviour,
then **verifies it actually fails (and compiles) on the unfixed code** — if it passes or won't
compile, the test is repaired with the `go test` output as feedback (≤3 attempts), and if still
invalid the oracle is skipped (graceful). When valid, the failing test is pre-written into each
candidate's worktree (true TDD); the agent must make it pass without weakening it (we restore
our canonical copy before checking). `repro_pass` then becomes the strongest selection signal —
turning "matched the right files" into "actually fixed, verified by a test."

Gated on Go being available, so the lean batch-eval mode skips it. Works best on bugs with an
obvious reproduction (reject/accept a specific input); on subtle bugs it abstains by design.

## Key design choices

| Choice | Reason |
|--------|--------|
| LangGraph `StateGraph` | Phases, edges, and loops are explicit and inspectable — the heal loop and candidate fan-out are first-class, not ad-hoc control flow |
| LangChain chat models | Provider differences (tool-calling, structured output) handled by the framework |
| Fixed phases (not a free agent) | Reliable, interpretable, debuggable |
| BM25 retrieval (pure Python) | Lightweight RAG, no embedding service or vector DB; enough at these repos' scale |
| Reproduction-test oracle | Verifies the fix actually works, instead of trusting "existing tests still pass" |
| Multi-candidate + ranked select | Agentless-style sampling; pick the candidate that passes the oracle and the suite |
| Git worktrees per candidate | True parallel isolation |
| `edit_file` exact-match + `replace_lines` | Prevents hallucinated edits; `replace_lines` handles big embedded string literals |
| Command whitelist (`go`/`git`/`gofmt`/`golangci-lint`) | Sandboxes side effects |
| Project rules (`rules/*.md`) | Per-project Go conventions so output matches project style |
| `--base-commit` | Solve at the pre-fix state for fair, SWE-bench-style evaluation |

## Tools (the agent-computer interface)

`tools.py` exposes a small, LLM-friendly tool set via `make_tools(path)` (LangChain `@tool`s
bound to a worktree): `list_directory`, `read_file`, `search_code` (grep), `edit_file`
(exact-unique match), `replace_lines` (range edit for templates), `create_file`, and
`run_command` (whitelisted). Every tool returns an error string instead of raising, so a
failing tool never crashes the agent loop.

## Indexing & retrieval

`build_index.py` builds an offline, **LLM-free** index per repo (`indexes/{repo}/`):

- **`symbols.json`** — every top-level `func`/`type`/`const`/`var` with file, line, signature,
  doc comment.
- **`pr_examples.json`** — recent merged PRs (linked issue + changed-files summary) for style.

Retrieval is BM25 over these. If a repo has no pre-built index, the agent builds a symbol-only
index on the fly (no PR examples). Pre-built indexes ship for cobra and validator.

## How it compares

| System | Inspiration taken |
|--------|------------------|
| [Agentless](https://github.com/OpenAutoCoder/Agentless) | Fixed phases; sample multiple patches; reproduction tests + ranking |
| [SWE-agent](https://github.com/SWE-agent/SWE-agent) | ACI tools built for LLMs (not raw bash), with informative errors |
| [AutoCodeRover](https://github.com/AutoCodeRoverSG/auto-code-rover) | Structure-first localization over a symbol index |
| [Aider](https://aider.chat) | Repo map; project conventions as explicit rules |

The bet: a focused, readable pipeline that evaluators can understand and extend beats a
feature-complete but opaque autonomous agent.

## Extending

- **New repo rules** — add `rules/{name}.md` (package structure, error handling, test style).
- **New tool** — add a `_tool_{name}` to `ToolExecutor`, expose it in `make_tools()`, and
  document it in `prompts/patch_candidate.md`.
- **New model** — `--model <name>` (for Azure, the deployment name).

## Project layout

```
solve.py              CLI (explain / implement / review)
build_index.py        Offline index builder (symbols + PR examples)
docker-entrypoint.sh  Docker entrypoint (bare issue → implement)
agent/
  pipeline.py         LangGraph StateGraph — all phases, wired per mode
  llm_client.py       make_chat_model() → LangChain chat model
  tools.py            ToolExecutor + make_tools() (the ACI)
  github_client.py    issue/PR fetch, clone, branch pinning
  indexer.py          symbol + PR-example index construction
  retrieval.py        BM25 retrieval
  repomap.py          lightweight repo map for localization
  go_utils.py         Go toolchain (local or Docker) + git worktrees
prompts/              one Markdown prompt per phase
rules/                per-repo convention rules
indexes/              pre-built indexes (cobra, validator)
eval/                 SWE-bench-style evaluation harness + dataset + results
sample_outputs/       captured demo runs
```
