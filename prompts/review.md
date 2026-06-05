You are reviewing a pull request for an open-source Go project, as a careful maintainer would.

## Pull Request

**Title:** {pr_title}
**URL:** {pr_url}

**Description:**
{pr_body}

## Linked Issue (what the PR is meant to solve)

{linked_issue}

## Project Rules / Conventions

{project_rules}

## The Diff

```diff
{diff}
```

## Changed Files — full context (pre-change)

{changed_file_contents}

## Task

Review the PR and write a structured Markdown review with these sections:

## Summary
1–2 sentences: what the PR does.

## Does it address the issue?
State clearly whether the change actually resolves the linked issue (or, if no issue is
linked, whether the change is coherent and well-motivated). Note anything it misses.

## Correctness
Concrete bugs, edge cases, or regressions. For each: the file/line and why it's a problem.
If you find none, say so explicitly.

## Conventions
Where the change does or does not follow the project's style and patterns (error handling,
naming, test style, minimal-diff discipline).

## Tests
Is the change adequately tested? Are the new tests meaningful (would they fail without the
fix)? What's missing?

## Suggestions
Specific, actionable improvements (smaller scope, better naming, extra test cases, etc.).

## Verdict
One of: **Approve** / **Approve with nits** / **Request changes** — followed by a one-line
rationale.

Be specific and reference the diff. Be honest: praise what's good, flag what's risky. Do not
rewrite the whole PR — give targeted feedback.
