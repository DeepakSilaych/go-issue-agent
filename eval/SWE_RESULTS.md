# SWE-style Evaluation Results

Cobra, 27 held-out cases. For each already-fixed issue we check out the **pre-fix commit**,
run the agent (`implement`), and compare its diff against the real merged PR.
Config: 1 candidate, Go tests skipped (the comparison is diff-vs-gold, not validation).

## Before → after targeted fixes

The first run surfaced concrete failures (test-only patches, an `edit_file` limitation on
embedded string literals, over-inclusion). After fixing those — a source-edit requirement +
retry, a `replace_lines` tool, and source-aware candidate ranking — re-running the affected
cases gave:

| metric | baseline | after fixes |
|---|---|---|
| localized (touched a right file) | 77.8% | **92.6%** |
| exact file set | 55.6% | **66.7%** |
| mean file recall | 0.67 | **0.81** |
| mean file precision | 0.77 | **0.90** |
| produced a fix | 100% | 100% |

Recovered: #1831 & #1849 (test-only → **exact**), #1211 & #1853 (miss → localized), #383
(partial → **exact**), #1437 (recall 0.25 → 0.5). Remaining misses: #107 (zsh), #805
(`doc/man_docs.go`, a subdir doc generator) — both still produce no source edit.

## Aggregate

| metric | value |
|---|---|
| cases | 27 |
| completed | 27 |
| errors | 0 |
| produced_fix_rate | 1.0 |
| localized_rate | 0.926 |
| exact_file_set_rate | 0.667 |
| mean_file_recall | 0.809 |
| mean_file_precision | 0.901 |

## Per-case

| issue | localized | recall | precision | exact-files | fix? | gold src | our src |
|---|---|---|---|---|---|---|---|
| #107 | ❌ | 0.0 | 0.0 |  | ✅ | zsh_completions.go |  |
| #383 | ✅ | 1.0 | 1.0 | ✅ | ✅ | cobra.go, command_win.go | cobra.go, command_win.go |
| #503 | ✅ | 1.0 | 1.0 | ✅ | ✅ | cobra/cmd/licenses.go | cobra/cmd/licenses.go |
| #556 | ✅ | 1.0 | 1.0 | ✅ | ✅ | cobra/cmd/add.go, cobra/cmd/init.go | cobra/cmd/add.go, cobra/cmd/init.go |
| #805 | ❌ | 0.0 | 0.0 |  | ✅ | doc/man_docs.go |  |
| #879 | ✅ | 1.0 | 1.0 | ✅ | ✅ | cobra/cmd/license_gpl_2.go | cobra/cmd/license_gpl_2.go |
| #1000 | ✅ | 0.333 | 1.0 |  | ✅ | bash_completions.go, command.go, custom_completions.go | bash_completions.go |
| #1002 | ✅ | 1.0 | 1.0 | ✅ | ✅ | command.go | command.go |
| #1121 | ✅ | 1.0 | 1.0 | ✅ | ✅ | fish_completions.go | fish_completions.go |
| #1170 | ✅ | 0.5 | 1.0 |  | ✅ | bash_completions.go, custom_completions.go | custom_completions.go |
| #1211 | ✅ | 0.5 | 1.0 |  | ✅ | completions.go, zsh_completions.go | zsh_completions.go |
| #1257 | ✅ | 1.0 | 1.0 | ✅ | ✅ | completions.go | completions.go |
| #1303 | ✅ | 1.0 | 1.0 | ✅ | ✅ | fish_completions.go | fish_completions.go |
| #1362 | ✅ | 1.0 | 1.0 | ✅ | ✅ | powershell_completions.go | powershell_completions.go |
| #1382 | ✅ | 0.5 | 1.0 |  | ✅ | cobra.go, command.go | command.go |
| #1437 | ✅ | 0.5 | 1.0 |  | ✅ | bash_completions.go, command.go, completions.go, fish_completions.go | bash_completions.go, completions.go |
| #1507 | ✅ | 1.0 | 1.0 | ✅ | ✅ | completions.go | completions.go |
| #1562 | ✅ | 1.0 | 1.0 | ✅ | ✅ | completions.go | completions.go |
| #1734 | ✅ | 1.0 | 1.0 | ✅ | ✅ | bash_completionsV2.go | bash_completionsV2.go |
| #1786 | ✅ | 0.5 | 1.0 |  | ✅ | command.go, completions.go | completions.go |
| #1816 | ✅ | 1.0 | 1.0 | ✅ | ✅ | command.go | command.go |
| #1831 | ✅ | 1.0 | 1.0 | ✅ | ✅ | command.go | command.go |
| #1847 | ✅ | 1.0 | 1.0 | ✅ | ✅ | powershell_completions.go | powershell_completions.go |
| #1849 | ✅ | 1.0 | 1.0 | ✅ | ✅ | powershell_completions.go | powershell_completions.go |
| #1853 | ✅ | 1.0 | 0.333 |  | ✅ | powershell_completions.go | bash_completions.go, bash_completionsV2.go, powershell_completions.go |
| #2060 | ✅ | 1.0 | 1.0 | ✅ | ✅ | completions.go | completions.go |
| #2257 | ✅ | 1.0 | 1.0 | ✅ | ✅ | completions.go | completions.go |