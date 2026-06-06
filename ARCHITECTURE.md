# Architecture

`go-issue-agent` is a [LangGraph](https://langchain-ai.github.io/langgraph/) `StateGraph`
(`agent/pipeline.py`). Each phase is a graph node with one bounded job. The only open-ended
loops are the patch and self-heal steps, which run as LangChain `create_agent` (ReAct) loops
with a capped step budget. The LLM layer is LangChain chat models (`langchain-anthropic`,
`langchain-groq`, `langchain-openai`), so the framework handles provider differences instead of
custom glue code.

`build_graph(mode)` wires one of three sub-graphs from the same set of nodes.

## The implement graph

```
START
  v
setup -> retrieve -> localize -> plan -> reproduce
                                            |
                           (Send fan-out, 1 per candidate)
                                            v
                                    patch_candidate xN   (each in its own git worktree,
                                            |             driven by create_agent / TDD)
                                            v
                                      select_patch  (rank: repro_pass, tests,
                                            |         source-edit, smallest diff)
                                            v
                                        validate  <-------+
                                            |             |
                                 (tests fail & go avail)  |
                                            v             |
                                        self_heal --------+
                                            |
                                   (pass / no go / out of rounds)
                                            v
                                         summary -> END
```

The other two modes reuse the same nodes:

- `explain`: `setup -> retrieve -> localize -> plan -> explain -> END`
- `review`: `setup_review -> review -> END` (the input is a `PullRequest`, not an `Issue`)

## Phases

1. **Retrieve** (`retrieval.py`): pure-Python BM25 over the repo's symbol index ranks Go
   symbols against the issue text, and ranks similar past merged PRs for style and file hints.
   No embeddings, no vector store.
2. **Localize** (`prompts/localize_deep.md`): one structured call (`with_structured_output`)
   returns precise edit, new-code, and test locations at file, function, and line level. If
   structured output fails, it falls back to a text prompt and a JSON parse.
3. **Plan** (`prompts/plan.md`): the root cause and a step-by-step fix strategy, grounded in
   the located files, the similar PRs, and the repo's convention rules. `--guidance` is
   injected here.
4. **Reproduce** (`prompts/reproduce.md`): the oracle, described below.
5. **Multi-patch**: a `Send` fan-out runs N candidates (default 3), each in its own git
   worktree with a different strategy (minimal, more coverage, or pattern-matching), driven by
   a `create_agent` tool loop. `select_patch` ranks them by `(repro_pass, tests_pass,
   has_source_edit, smallest_diff)` and applies the winner.
6. **Validate and self-heal**: runs `go build`, `go test`, and `go vet`. When tests fail, a
   conditional `validate` to `self_heal` loop feeds the failures back to the agent for up to 3
   rounds. A round that crashes is rolled back so it cannot pollute the diff.
7. **Summary**: writes the PR title and body, plus the combined `pr.md` (title, description,
   and diff in one file).

If there is no local `go` binary, the Go steps run inside the official `golang` Docker image,
with the tag taken from the target repo's `go.mod`. So it works without a local Go install.

## The reproduction-test oracle

Before patching, `reproduce` asks the model for a Go test that asserts the correct behaviour,
then checks that the test actually fails (and compiles) on the unfixed code. If the test passes
or will not compile, it is repaired using the `go test` output as feedback, up to 3 attempts.
If it is still invalid, the oracle is skipped and the run continues without it.

When the test is valid, it is written into each candidate's worktree before the agent starts, so
the agent works against a failing test (true TDD). The agent has to make it pass without
weakening it; the run restores the canonical copy of the test before checking. `repro_pass` then
becomes the strongest selection signal, which turns "matched the right files" into "fixed it,
verified by a test."

The oracle is gated on Go being available, so the lean batch-evaluation mode skips it. It works
best on bugs with an obvious reproduction, such as rejecting or accepting a specific input. On
subtle bugs it abstains rather than trust a test it could not verify.

## Key design choices

| Choice | Reason |
|--------|--------|
| LangGraph `StateGraph` | Phases, edges, and loops are explicit and inspectable. The heal loop and candidate fan-out are graph constructs, not ad-hoc control flow. |
| LangChain chat models | The framework handles provider tool-calling and structured output. |
| Fixed phases instead of a free agent | Reliable, interpretable, and easy to debug. |
| BM25 retrieval in pure Python | No embedding service or vector DB, which is enough at these repos' scale. |
| Reproduction-test oracle | Verifies the fix works, instead of trusting that the existing tests still pass. |
| Multiple candidates, ranked | Samples a few patches and keeps the one that passes the oracle and the suite. |
| Git worktrees per candidate | Real parallel isolation. |
| `edit_file` exact match plus `replace_lines` | Prevents hallucinated edits; `replace_lines` handles large embedded string literals. |
| Command whitelist (`go`, `git`, `gofmt`, `golangci-lint`) | Sandboxes side effects. |
| Project rules (`rules/*.md`) | Per-repo Go conventions, so output matches the project's style. |
| `--base-commit` | Solves at the pre-fix state for fair evaluation against a merged PR. |

## Tools (the agent-computer interface)

`tools.py` gives the agent a small tool set via `make_tools(path)`, which returns LangChain
`@tool` functions bound to a worktree: `list_directory`, `read_file`, `search_code` (grep),
`edit_file` (exact unique match), `replace_lines` (range edit for templates), `create_file`, and
`run_command` (whitelisted). Every tool returns an error string instead of raising, so a failing
tool never crashes the agent loop.

## Indexing and retrieval

`build_index.py` builds an offline, LLM-free index per repo under `indexes/{repo}/`:

- `symbols.json`: every top-level `func`, `type`, `const`, and `var`, with file, line,
  signature, and doc comment.
- `pr_examples.json`: recent merged PRs with the linked issue and a changed-files summary, used
  for style.

Retrieval is BM25 over these two files. If a repo has no pre-built index, the agent builds a
symbol-only index on the fly, without the PR examples (those need the network). Indexes ship for
cobra and validator.

## How it compares

| System | What this borrows |
|--------|------------------|
| [Agentless](https://github.com/OpenAutoCoder/Agentless) | Fixed phases, sampling several patches, reproduction tests, ranking. |
| [SWE-agent](https://github.com/SWE-agent/SWE-agent) | Tools built for an LLM, not raw bash, with informative errors. |
| [AutoCodeRover](https://github.com/AutoCodeRoverSG/auto-code-rover) | Structure-first localization over a symbol index. |
| [Aider](https://aider.chat) | The repo map, and project conventions as explicit rules. |

The goal is a pipeline that is easy to read and extend, rather than a more capable but opaque
autonomous agent.

## Extending

- New repo rules: add `rules/{name}.md` (package structure, error handling, test style).
- New tool: add a `_tool_{name}` to `ToolExecutor`, expose it in `make_tools()`, and document
  it in `prompts/patch_candidate.md`.
- New model: pass `--model <name>` (for Azure, the deployment name).

## Project layout

```
solve.py              CLI (explain / implement / review)
build_index.py        Offline index builder (symbols + PR examples)
docker-entrypoint.sh  Docker entrypoint (bare issue -> implement)
agent/
  pipeline.py         LangGraph StateGraph: all phases, wired per mode
  llm_client.py       make_chat_model() -> LangChain chat model
  tools.py            ToolExecutor + make_tools() (the agent's tools)
  github_client.py    issue/PR fetch, clone, branch pinning
  indexer.py          symbol + PR-example index construction
  retrieval.py        BM25 retrieval
  repomap.py          lightweight repo map for localization
  go_utils.py         Go toolchain (local or Docker) + git worktrees
prompts/              one Markdown prompt per phase
rules/                per-repo convention rules
indexes/              pre-built indexes (cobra, validator)
eval/                 SWE-bench-style evaluation harness, dataset, results
sample_outputs/       captured demo runs
```
