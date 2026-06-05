You are implementing a code fix for a GitHub issue.

## Issue

**Title:** {issue_title}
**URL:** {issue_url}

**Body:**
{issue_body}

## Project Rules

{project_rules}

## Fix Plan

{fix_plan}

## Task

Implement the fix using the available tools:

- `read_file(path)` — read a file (use this before editing)
- `search_code(pattern)` — grep for patterns
- `list_directory(path)` — explore directories
- `edit_file(path, old_content, new_content)` — make precise edits
- `create_file(path, content)` — create new files
- `run_command(command)` — run `go build`, `go test`, `go vet`, `gofmt`

## Workflow

1. **Read** all files you plan to modify (use read_file first).
2. **Implement** the changes described in the fix plan using edit_file.
3. **Verify** by running `go build ./...` — fix any compilation errors.
4. **Test** by running `go test ./...` — fix any failures.
5. **Format** with `gofmt -l .` to check formatting.

When all tests pass and the build succeeds, respond with:

```
DONE: <one-sentence summary of what was changed>
```

If you encounter an unexpected blocker that prevents completing the fix, respond with:

```
BLOCKED: <explanation of what is preventing completion>
```
