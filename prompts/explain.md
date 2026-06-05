You are explaining a GitHub issue and a proposed solution to a developer who will review it.

## Issue

**Title:** {issue_title}
**URL:** {issue_url}

**Body:**
{issue_body}

## Project Rules

{project_rules}

## Relevant Code (the files identified as most relevant)

{file_contents}

## Proposed Fix Plan (draft)

{fix_plan}

## Located Edit Points

{edit_locations}

## Task

Write a clear, reviewer-facing explanation in Markdown with these sections:

## Issue
Restate the issue in plain language — what's the observed behaviour and why it's a problem.

## Root Cause
Pinpoint exactly where and why the problem occurs, referencing specific files/functions.

## Proposed Solution
Describe the fix approach in prose (not a diff): what changes, in which file(s)/function(s),
and why this is the right, minimal fix. Mention any alternative approaches and trade-offs.

## Files to Change
A short bullet list of the files that should be touched (source and tests).

## How to Validate
The concrete `go test`/build commands and the behaviour to check.

## Confidence
One or two sentences: how confident you are, and what (if anything) a human should double-check.

Be precise and concise. Do not write the actual code — this is an explanation, not an implementation.
