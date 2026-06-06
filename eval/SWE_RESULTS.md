# SWE-style evaluation results

27 held-out cobra issues. For each already-fixed issue, the agent runs at the commit
before the fix landed, and its output is compared to the real merged PR. Config: 1
candidate, Go tests skipped, since this compares the diff against the gold PR rather than
running tests.

## Results

| metric | value |
|---|---|
| cases | 27 |
| produced a fix | 100% |
| localized (edited a file the PR changed) | 93% |
| exact file set (edited exactly the PR's Go files) | 67% |
| mean file recall | 0.809 |
| mean file precision | 0.901 |

## Per-case

| issue | localized | recall | precision | exact files | fix | gold src | our src |
|---|---|---|---|---|---|---|---|
| #107 | no | 0.0 | 0.0 |  | yes | zsh_completions.go |  |
| #383 | yes | 1.0 | 1.0 | yes | yes | cobra.go, command_win.go | cobra.go, command_win.go |
| #503 | yes | 1.0 | 1.0 | yes | yes | cobra/cmd/licenses.go | cobra/cmd/licenses.go |
| #556 | yes | 1.0 | 1.0 | yes | yes | cobra/cmd/add.go, cobra/cmd/init.go | cobra/cmd/add.go, cobra/cmd/init.go |
| #805 | no | 0.0 | 0.0 |  | yes | doc/man_docs.go |  |
| #879 | yes | 1.0 | 1.0 | yes | yes | cobra/cmd/license_gpl_2.go | cobra/cmd/license_gpl_2.go |
| #1000 | yes | 0.333 | 1.0 |  | yes | bash_completions.go, command.go, custom_completions.go | bash_completions.go |
| #1002 | yes | 1.0 | 1.0 | yes | yes | command.go | command.go |
| #1121 | yes | 1.0 | 1.0 | yes | yes | fish_completions.go | fish_completions.go |
| #1170 | yes | 0.5 | 1.0 |  | yes | bash_completions.go, custom_completions.go | custom_completions.go |
| #1211 | yes | 0.5 | 1.0 |  | yes | completions.go, zsh_completions.go | zsh_completions.go |
| #1257 | yes | 1.0 | 1.0 | yes | yes | completions.go | completions.go |
| #1303 | yes | 1.0 | 1.0 | yes | yes | fish_completions.go | fish_completions.go |
| #1362 | yes | 1.0 | 1.0 | yes | yes | powershell_completions.go | powershell_completions.go |
| #1382 | yes | 0.5 | 1.0 |  | yes | cobra.go, command.go | command.go |
| #1437 | yes | 0.5 | 1.0 |  | yes | bash_completions.go, command.go, completions.go, fish_completions.go | bash_completions.go, completions.go |
| #1507 | yes | 1.0 | 1.0 | yes | yes | completions.go | completions.go |
| #1562 | yes | 1.0 | 1.0 | yes | yes | completions.go | completions.go |
| #1734 | yes | 1.0 | 1.0 | yes | yes | bash_completionsV2.go | bash_completionsV2.go |
| #1786 | yes | 0.5 | 1.0 |  | yes | command.go, completions.go | completions.go |
| #1816 | yes | 1.0 | 1.0 | yes | yes | command.go | command.go |
| #1831 | yes | 1.0 | 1.0 | yes | yes | command.go | command.go |
| #1847 | yes | 1.0 | 1.0 | yes | yes | powershell_completions.go | powershell_completions.go |
| #1849 | yes | 1.0 | 1.0 | yes | yes | powershell_completions.go | powershell_completions.go |
| #1853 | yes | 1.0 | 0.333 |  | yes | powershell_completions.go | bash_completions.go, bash_completionsV2.go, powershell_completions.go |
| #2060 | yes | 1.0 | 1.0 | yes | yes | completions.go | completions.go |
| #2257 | yes | 1.0 | 1.0 | yes | yes | completions.go | completions.go |
