You are performing deep hierarchical localization for a GitHub issue fix.

## Issue

**Title:** {issue_title}
**URL:** {issue_url}

**Body:**
{issue_body}

## Retrieved Symbols (BM25 match on issue text)

{retrieved_symbols}

## Repository Map

{repo_map}

## Task

Using the retrieved symbols and repo map, identify with surgical precision:

1. **Edit locations** — The exact function(s)/block(s) that need to change:
   ```
   file.go:LineN  func/type name  — what needs to change
   ```

2. **New locations** — Where new code should be added (file + after which existing symbol):
   ```
   file.go  after L250 (func SomeFunc)  — add new func/type here
   ```

3. **Test locations** — Exact test file and pattern to follow:
   ```
   file_test.go:LineN  func TestXxx  — follow this test pattern
   ```

4. **Context files** — Files to read for understanding (but not modify):
   ```
   file.go  — needed to understand interface / calling pattern
   ```

Return a JSON object:
```json
{{
  "edit_locations": [{{"file": "x.go", "line": 42, "symbol": "funcName", "reason": "..."}}],
  "new_locations":  [{{"file": "x.go", "after_line": 80, "after_symbol": "funcName", "reason": "..."}}],
  "test_locations": [{{"file": "x_test.go", "line": 100, "symbol": "TestXxx", "reason": "follow this pattern"}}],
  "context_files":  ["y.go"],
  "reasoning": "one paragraph summary"
}}
```

Be precise — only list locations you are confident about.
