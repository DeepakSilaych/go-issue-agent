You are planning a code fix for a GitHub issue.

## Issue

**Title:** {issue_title}
**URL:** {issue_url}

**Body:**
{issue_body}

## Project Rules

{project_rules}

## Relevant File Contents

{file_contents}

## Task

Write a precise, step-by-step fix plan. Structure it as:

### Root Cause
One paragraph explaining exactly what is wrong and where.

### Fix Strategy
A numbered list of concrete changes needed:
1. File `path/to/file.go`: What to change and why
2. File `path/to/test.go`: What test to add/modify

### Validation
How to verify the fix is correct:
- Which `go test` command to run
- What behavior to check

Keep the plan focused. Do not suggest refactoring unrelated code.
If the issue is unclear, state your interpretation explicitly.
