# go-issue-agent

An agentic AI system that solves GitHub issues from open-source Go projects and generates production-quality code changes.

Given a GitHub issue URL, the agent: clones the repo, maps the codebase, identifies relevant files, plans a targeted fix, implements it via a tool-use loop, runs `go test`, and produces a diff and pull request summary.

---

## Architecture

The system is a **4-phase fixed pipeline** backed by Claude's tool-use API for the implementation step. The pipeline is intentionally simple: each phase has a specific, bounded goal — no free-form autonomous loops.

```
Issue URL
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 1: LOCALIZE                                          │
│  Build a repo map (file tree + Go symbol index).            │
│  Ask Claude: which files are relevant to this issue?        │
│  → primary_files, secondary_files, test_files               │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 2: PLAN                                              │
│  Load the relevant file contents.                           │
│  Ask Claude: what is the root cause and fix strategy?       │
│  → root_cause, step-by-step fix, validation command         │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 3: PATCH  (tool-use loop, max 30 turns)              │
│  Claude implements the fix using tools:                     │
│    read_file, search_code, edit_file,                       │
│    create_file, run_command (go build/test/vet)             │
│  Loop continues until "DONE:" or "BLOCKED:" signal.         │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 4: VALIDATE                                          │
│  Run go build ./... and go test ./...                       │
│  Generate PR title + body from diff + test results.         │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
              output/issue-{N}/
                pr_summary.md
                changes.patch
                result.json
```

### Key Design Choices

| Choice | Reason |
|--------|--------|
| Fixed 4-phase pipeline | Reliable, interpretable, easy to debug vs. free-form agent |
| Repo map with symbol index | Gives Claude codebase structure without loading every file |
| Project-specific rules (`rules/*.md`) | Encodes Go/cobra/gin conventions so Claude follows the project style |
| Tool-use loop only in patch phase | Other phases need one focused answer, not exploration |
| `edit_file` requires exact match | Prevents hallucinated edits; forces Claude to read before editing |
| Only `go`/`git` commands allowed | Sandboxes side effects to safe operations |

---

## Supported Repositories

The agent ships with project-specific convention rules for:

- `spf13/cobra` — CLI framework
- `gin-gonic/gin` — HTTP framework
- `go-playground/validator` — struct validation
- `golangci/golangci-lint` — linter aggregator

Works on any Go repository; rules files improve output quality for the above.

---

## Setup

### Requirements

- Python 3.9+
- Git
- Go 1.21+ (for running tests in the target repo)
- An LLM API key (Anthropic **or** Groq free tier)

### Install

```bash
git clone https://github.com/your-username/go-issue-agent
cd go-issue-agent
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# Edit .env — add at least one of ANTHROPIC_API_KEY or GROQ_API_KEY
```

`.env`:
```
# Option A: Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Option B: Groq (free tier) — https://console.groq.com
GROQ_API_KEY=gsk_...

# Optional: higher GitHub API rate limit
GITHUB_TOKEN=ghp_...
```

---

## Usage

```bash
# Anthropic (default)
python solve.py --issue https://github.com/spf13/cobra/issues/2396

# Groq free tier
python solve.py --issue 2396 --provider groq

# Groq with a specific model
python solve.py --issue 2396 --provider groq --model llama-3.3-70b-versatile

# Different repo
python solve.py --issue https://github.com/gin-gonic/gin/issues/3988

# Custom workspace and output directories
python solve.py --issue 2396 --workspace ./repos --output ./results
```

### Options

```
--issue      GitHub issue URL or number (required)
--repo       GitHub repo owner/name, e.g. spf13/cobra (default: spf13/cobra)
--provider   LLM provider: anthropic or groq (default: anthropic)
--model      Model name (default: claude-sonnet-4-6 / llama-3.3-70b-versatile)
--workspace  Directory to clone repos into (default: ./workspace)
--output     Directory for output artifacts (default: ./output)
```

### Groq free tier notes

Groq's free tier supports tool/function calling on `llama-3.3-70b-versatile` (128k context).
Rate limits apply (~30 req/min on free tier), so complex issues may slow down during the
patch loop. The default model is a good balance of quality and speed.

Available Groq models for this tool: `llama-3.3-70b-versatile` (recommended), `llama3-70b-8192`.

---

## Output

For each run, the agent writes to `output/issue-{N}/`:

| File | Contents |
|------|----------|
| `pr_summary.md` | PR title and body, ready to paste into GitHub |
| `changes.patch` | `git diff HEAD` of the changes made |
| `result.json` | Full structured result: localization, plan, test results, etc. |

The modified repo is left on a branch named `fix/issue-{N}` in `workspace/{repo-name}/`.

---

## Sample Output

See [`sample_outputs/cobra_issue_2396.md`](sample_outputs/cobra_issue_2396.md) for a complete trace of the agent solving cobra#2396 (Add `NoDuplicateArgs` validator):

- Phase 1 identifies `args.go` and `args_test.go` as the only relevant files
- Phase 2 plans a minimal addition following cobra's existing validator pattern
- Phase 3 reads the files, adds the function and tests, confirms `go test ./...` passes
- Phase 4 generates a PR title and body in cobra's style

---

## Project Structure

```
go-issue-agent/
├── solve.py              # CLI entry point
├── requirements.txt
├── .env.example
│
├── agent/
│   ├── pipeline.py       # 4-phase orchestrator
│   ├── tools.py          # Tool definitions + ToolExecutor
│   ├── github_client.py  # Issue fetching, repo cloning
│   └── repomap.py        # Repository map builder
│
├── prompts/              # Phase-specific prompts (loaded at runtime)
│   ├── system.md         # System prompt for the patch agent
│   ├── localize.md
│   ├── plan.md
│   ├── patch.md
│   └── validate.md
│
├── rules/                # Project-specific conventions
│   ├── cobra.md
│   ├── gin.md
│   ├── validator.md
│   └── golangci-lint.md
│
└── sample_outputs/
    └── cobra_issue_2396.md
```

---

## How It Compares to Other Systems

This system is deliberately simple. The architecture borrows from:

| System | Inspiration taken |
|--------|------------------|
| [Agentless](https://github.com/OpenAutoCoder/Agentless) | Fixed phases (localize → plan → patch) beat free-form agents for reliability |
| [SWE-agent](https://github.com/SWE-agent/SWE-agent) | ACI-style tools built for LLMs (not raw bash), with informative error messages |
| [AutoCodeRover](https://github.com/AutoCodeRoverSG/auto-code-rover) | AST-aware repo map for structure-first localization |
| [Aider](https://aider.chat) | Repo map concept; project conventions as explicit rules |

The key bet: a focused, readable 4-phase pipeline that evaluators can understand and extend is better than a feature-complete but opaque autonomous agent.

---

## Extending the System

### Add rules for a new repository

Create `rules/{repo-name}.md` following the pattern in `rules/cobra.md`. Include:
- Package structure
- Error handling conventions
- Test style
- Naming patterns
- What to avoid

### Add a new tool

1. Add the tool definition to `TOOL_DEFINITIONS` in `agent/tools.py`
2. Add the `_tool_{name}` method to `ToolExecutor`
3. The patch prompt in `prompts/patch.md` documents available tools — update it

### Change the model

Pass `--model claude-opus-4-8` for higher quality on complex issues, or `claude-haiku-4-5-20251001` for speed.

---

## Limitations

- **Single-patch generation**: The agent makes one attempt per phase. Production systems sample N patches and rank by test results (Agentless-style). This is a straightforward extension.
- **No browser/web access**: Issues that link to external docs or require understanding web APIs are handled by the LLM's training knowledge only.
- **Sequential phases**: Phases run in order; an error in localization cascades. Adding retry logic to each phase is a natural next step.
- **Go only**: The tool restrictions (`go`/`git` commands only) and rules are Go-specific.
