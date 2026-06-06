You are writing a **reproduction test** for a GitHub issue, before the fix exists.

## Issue

**Title:** {issue_title}
**URL:** {issue_url}

**Body:**
{issue_body}

## Relevant Code

{file_contents}

## Fix Plan (for context — the fix does NOT exist yet)

{fix_plan}

## Task

Write a single Go test that **reproduces the bug**:

- It must **FAIL on the current (unfixed) code** and **PASS once the issue is fixed**
  (a fail-to-pass test). Assert the *correct/expected* behaviour — so it fails today.
- It must **compile** against the current codebase: use real exported/!exported symbols
  exactly as they appear in the relevant code, the correct package name, and only imports
  that exist.
- Follow the project's testing conventions (standard `testing`, table-driven if natural,
  `t.Errorf`/`t.Fatalf`, no external assertion libraries).
- Keep it self-contained: a complete file with `package`, imports, and one `func TestXxx`.
- Do NOT attempt to fix the bug. Only write the test.

Return JSON:
```json
{{
  "file": "repro_issue_<N>_test.go",
  "test_name": "TestReproIssueXxx",
  "package": "cobra",
  "code": "package cobra\n\nimport (...)\n\nfunc TestReproIssueXxx(t *testing.T) {{ ... }}"
}}
```

The `code` field is the FULL file content. The `test_name` must be the exact function name.
If you genuinely cannot write a compiling reproduction (e.g. the issue is about docs or
non-testable output), set `test_name` to `""`.
