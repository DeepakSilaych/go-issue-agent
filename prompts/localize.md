You are analyzing a GitHub issue to identify which files in the repository are relevant to the fix.

## Issue

**Title:** {issue_title}
**URL:** {issue_url}

**Body:**
{issue_body}

## Repository Map

{repo_map}

## Task

Based on the issue description and the repository map above, identify:

1. **Primary files** — Files that almost certainly need to be modified to fix this issue.
2. **Secondary files** — Files that provide context (related tests, interfaces, callers) but may not need modification.
3. **Test files** — Existing test files for the primary files.

Return your answer as a JSON object with this exact structure:
```json
{{
  "primary_files": ["path/to/file.go"],
  "secondary_files": ["path/to/other.go"],
  "test_files": ["path/to/file_test.go"],
  "reasoning": "Brief explanation of why these files are relevant"
}}
```

Focus on accuracy over completeness. It's better to list 2-3 highly relevant files than 10 loosely relevant ones.
Only include files that exist in the repository map above.
