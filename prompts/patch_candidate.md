You are implementing a specific code fix. This is candidate {candidate_id} of {total_candidates}.

## Issue

**Title:** {issue_title}
**Body:** {issue_body}

## Project Rules

{project_rules}

## Fix Plan

{fix_plan}

## Candidate Strategy

{candidate_strategy}

## Similar Past PRs (style reference)

{similar_prs}

## Precise Edit Locations

{edit_locations}

## Task

Implement the fix using tools. Candidate {candidate_id} should follow the strategy above.

**Tools available:**
- `read_file(path, start_line?, end_line?)` — always read before editing
- `search_code(pattern, path?)` — grep for patterns
- `edit_file(path, old_content, new_content)` — precise exact-match replacement
- `replace_lines(path, start_line, end_line, new_content)` — replace a line range; use this
  when `edit_file` can't match (e.g. editing inside a large embedded string literal/template)
- `create_file(path, content)` — new files
- `run_command(command)` — `go build ./...`, `go test ./...`, `go vet ./...`, `gofmt -w .`

**Workflow:**
1. Read every file you will modify first
2. Implement the fix by **changing source code** (a `.go` file that is NOT a `_test.go` file).
   Then, if appropriate, add or update tests.
3. Try `go build ./...` — if Go is available, fix any errors
4. Try `go vet ./...` and `go test ./...` — if Go is available, fix failures
5. If Go is NOT available, skip steps 3-4 and still respond DONE

**CRITICAL — a fix must change SOURCE code, not only tests.**
The issue describes broken *behaviour*; behaviour lives in the non-test `.go` files
(e.g. `command.go`, `completions.go`, `powershell_completions.go`). Editing or adding only a
`_test.go` file does NOT fix the issue — a test that asserts the current (buggy) behaviour, or a
new test with no corresponding source change, is wrong. Always make the source change first; add a
test only to *cover* that source change.

**IMPORTANT: Always implement the code changes, even if you cannot run Go.
Never respond BLOCKED just because `go` or `gofmt` is not installed.
The code review step will handle validation.**

When done (with or without test execution) respond:
```
DONE: <one sentence describing the change>
```

Only respond BLOCKED if you genuinely cannot determine what code to write:
```
BLOCKED: <specific technical reason why the change cannot be determined>
```
