# Sample walkthrough: cobra#2396, add a `NoDuplicateArgs` validator

An annotated walkthrough of the five phases for
[cobra#2396](https://github.com/spf13/cobra/issues/2396), which requests a positional-argument
validator that rejects repeated values. It illustrates the shape of a run and the artifacts
produced; console formatting is condensed for readability.

**Command:**
```bash
python solve.py implement --issue https://github.com/spf13/cobra/issues/2396
```

---

## Phase 1: Retrieve

BM25 ranks the repo's symbol index against the issue text, and ranks similar merged PRs.

```
[phase 1/5] Retrieving relevant symbols and similar PRs...
  Symbols retrieved: 25
    args.go      L24  func NoArgs(cmd *Command, args []string) error
    args.go      L33  func OnlyValidArgs(cmd *Command, args []string) error
    args.go      L52  func ArbitraryArgs(cmd *Command, args []string) error
    args.go      L60  func MinimumNArgs(n int) PositionalArgs
    ...
  Similar PRs found: 2
    - #1644: args: add MatchAll to combine PositionalArgs validators
```

The validator family in `args.go` rises to the top, which is exactly the file to change.

---

## Phase 2: Localize (hierarchical)

Given the retrieved symbols and a repo map, the model returns precise locations:

```
[phase 2/5] Deep hierarchical localization...
  NEW    args.go  after L51 (func OnlyValidArgs)   add NoDuplicateArgs validator
  TEST   args_test.go  follow pattern at L120 (func TestOnlyValidArgs)
  CTX    command.go    shows how the Args field consumes a PositionalArgs
```

---

## Phase 3: Plan

```
[phase 3/5] Planning fix with PR context...
```

### Root Cause
Cobra ships several positional-argument validators (`NoArgs`, `OnlyValidArgs`, `MinimumNArgs`,
`MaximumNArgs`, `RangeArgs`, `ExactArgs`) but none that rejects **duplicate** argument values.
CLIs that accept a bounded set of values have no built-in way to forbid repetition.

### Fix Strategy
1. **`args.go`**: Add a new exported validator `NoDuplicateArgs(cmd *Command, args []string) error`,
   following the existing validator signature. It tracks seen values in a `map[string]bool` and
   returns a descriptive error on the first repeat.
2. **`args_test.go`**: Add a table-driven `TestNoDuplicateArgs` covering: no args, all-unique args,
   one duplicate, and all-duplicate inputs.

### Validation
```
go test ./... -run TestNoDuplicateArgs
go test ./...
```

---

## Phase 4: Multi-Patch (3 candidates, ranked by tests)

Three candidates run in parallel git worktrees, each with a different strategy, then are tested
and ranked (tests-pass first, then smallest diff):

```
[phase 4/5] Generating 3 patch candidates in parallel...
  Candidate 1 (minimal):          status=DONE  tests=PASS  diff=712 bytes
  Candidate 2 (more coverage):    status=DONE  tests=PASS  diff=1043 bytes
  Candidate 3 (pattern-match):    status=DONE  tests=PASS  diff=730 bytes
  Best candidate: #1 (tests pass, smallest diff)
```

The winning candidate's tool-use loop, condensed:

```
> read_file(path='args.go')
> search_code(pattern='func.*\(cmd \*Command, args \[\]string\) error')
  args.go:24: func NoArgs(cmd *Command, args []string) error {
  args.go:33: func OnlyValidArgs(cmd *Command, args []string) error {
> edit_file(path='args.go', ...)            -> Edited 'args.go' successfully.
> read_file(path='args_test.go')
> edit_file(path='args_test.go', ...)       -> Edited 'args_test.go' successfully.
> run_command(command='go build ./...')     -> exit_code: 0
> run_command(command='go test ./... -run TestNoDuplicateArgs')  -> ok
DONE: Added NoDuplicateArgs validator with table-driven tests.
```

---

## Phase 5: Validate + PR Summary

```
[phase 5/5] Validating and generating PR summary...
  Applied diff successfully.
  Build: PASS
  Tests: PASS
  Vet:   PASS
```

### Diff

```diff
diff --git a/args.go b/args.go
index a5c7c3e..b1d2f8e 100644
--- a/args.go
+++ b/args.go
@@ -49,6 +49,19 @@ func OnlyValidArgs(cmd *Command, args []string) error {
 	return nil
 }
 
+// NoDuplicateArgs returns an error if any of the provided arguments are repeated.
+func NoDuplicateArgs(cmd *Command, args []string) error {
+	seen := make(map[string]bool, len(args))
+	for _, arg := range args {
+		if seen[arg] {
+			return fmt.Errorf("invalid argument %q: duplicate argument", arg)
+		}
+		seen[arg] = true
+	}
+	return nil
+}
+
 // ArbitraryArgs never returns an error.
 func ArbitraryArgs(cmd *Command, args []string) error {
 	return nil
diff --git a/args_test.go b/args_test.go
index d4e92b1..7c3a1f2 100644
--- a/args_test.go
+++ b/args_test.go
@@ -118,3 +118,35 @@ func TestOnlyValidArgs(t *testing.T) {
 		})
 	}
 }
+
+func TestNoDuplicateArgs(t *testing.T) {
+	tests := []struct {
+		name    string
+		args    []string
+		wantErr bool
+	}{
+		{"no args", nil, false},
+		{"all unique args", []string{"foo", "bar", "baz"}, false},
+		{"one duplicate", []string{"foo", "bar", "foo"}, true},
+		{"all duplicates", []string{"foo", "foo"}, true},
+	}
+	for _, tt := range tests {
+		t.Run(tt.name, func(t *testing.T) {
+			c := &Command{Use: "c"}
+			err := NoDuplicateArgs(c, tt.args)
+			if (err != nil) != tt.wantErr {
+				t.Errorf("NoDuplicateArgs() error = %v, wantErr %v", err, tt.wantErr)
+			}
+		})
+	}
+}
```

### PR Summary

**PR Title:** `args: add NoDuplicateArgs positional argument validator`

**PR Body:**

## What
Adds a `NoDuplicateArgs` positional-argument validator that returns an error when the same
argument value appears more than once. It complements the existing validators in `args.go` for
commands that accept a fixed set of values but should not allow repetition.

## Why
CLIs with commands that accept a bounded set of positional arguments (e.g. resource names) have
no built-in way to prevent duplicates. `NoDuplicateArgs` fills the gap, following the same
pattern as the existing validators.

## Testing
- Added table-driven tests in `args_test.go`: no args, all-unique, one duplicate, all duplicates.
- `go build ./...`, `go vet ./...`, and `go test ./...` all pass.

Closes #2396

---

**result.json (excerpt):**
```json
{
  "tests_pass": true,
  "build_ok": true,
  "vet_ok": true,
  "diff_applied": true,
  "candidate_id": 1
}
```
