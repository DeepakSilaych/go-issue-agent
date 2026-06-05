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
- `edit_file(path, old_content, new_content)` — precise replacement
- `create_file(path, content)` — new files
- `run_command(command)` — `go build ./...`, `go test ./...`, `go vet ./...`, `gofmt -w .`

**Workflow:**
1. Read every file you will modify first
2. Implement ALL changes from the fix plan using edit_file / create_file
3. Try `go build ./...` — if Go is available, fix any errors
4. Try `go vet ./...` and `go test ./...` — if Go is available, fix failures
5. If Go is NOT available, skip steps 3-4 and still respond DONE

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
