You are generating a pull request summary for a completed code fix.

## Issue

**Title:** {issue_title}
**URL:** {issue_url}

**Body:**
{issue_body}

## Changes Made

{diff}

## Test Results

{test_results}

## Task

Generate a pull request title and body in the following format.
Match the style of the project's existing PRs (concise title, clear body with context).

---

**PR Title:** (one line, imperative mood, ≤72 chars)

**PR Body:**

## What

(1-3 sentences: what was broken and what was changed)

## Why

(1-2 sentences: why this is the right fix)

## Testing

(Describe how the fix was verified)

Closes #{issue_number}

---

Be factual and concise. Do not use marketing language.
