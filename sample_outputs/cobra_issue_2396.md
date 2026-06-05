# Sample Run: cobra#2396 — Add NoDuplicateArgs Validator

**Command:**
```bash
python solve.py --issue https://github.com/spf13/cobra/issues/2396
```

---

## Phase 1: Localize

The agent identified the following files as relevant:

**Primary files:**
- `args.go` — where all positional argument validators (`NoArgs`, `OnlyValidArgs`, `RangeArgs`, etc.) are defined

**Secondary files:**
- `command.go` — shows how `Args` field is used on `Command`

**Test files:**
- `args_test.go` — existing tests for all arg validators

**Reasoning:** The issue requests a new `NoDuplicateArgs` positional argument validator, following the same pattern as `OnlyValidArgs` and other validators in `args.go`.

---

## Phase 2: Fix Plan

### Root Cause

Cobra provides several built-in positional argument validators (`NoArgs`, `OnlyValidArgs`, `MinimumNArgs`, `MaximumNArgs`, `RangeArgs`, `ExactArgs`) but no validator that prevents duplicate argument values. Users building CLIs that accept a subset of values want to prevent the same value being passed twice.

### Fix Strategy

1. **`args.go`**: Add a new exported function `OnlyValidArgs` that wraps the existing validators — specifically, add `NoDuplicateArgs` following the same function signature as other validators:
   ```go
   func NoDuplicateArgs(cmd *Command, args []string) error
   ```
   The function should detect any repeated values in `args` and return a descriptive error.

2. **`args_test.go`**: Add table-driven tests covering:
   - No args (should pass)
   - All unique args (should pass)
   - One duplicate arg (should fail with clear message)
   - Multiple duplicate args (should fail)

### Validation

```
go test ./... -run TestNoDuplicateArgs
go test ./...
```

---

## Phase 3: Patch (Tool-Use Loop)

```
> read_file(path='args.go')
  [args.go  (120 lines)]

> search_code(pattern='func.*Args.*Command.*args.*string.*error')
  args.go:24: func NoArgs(cmd *Command, args []string) error {
  args.go:32: func OnlyValidArgs(cmd *Command, args []string) error {
  args.go:55: func ArbitraryArgs(cmd *Command, args []string) error {
  ...

> edit_file(path='args.go', old_content='...', new_content='...')
  Edited 'args.go' successfully.

> read_file(path='args_test.go')
  [args_test.go  (180 lines)]

> edit_file(path='args_test.go', old_content='...', new_content='...')
  Edited 'args_test.go' successfully.

> run_command(command='go build ./...')
  exit_code: 0

> run_command(command='go test ./... -run TestNoDuplicateArgs')
  stdout: ok  github.com/spf13/cobra
  exit_code: 0

> run_command(command='go test ./...')
  stdout: ok  github.com/spf13/cobra
  exit_code: 0

DONE: Added NoDuplicateArgs validator to args.go with tests in args_test.go; all tests pass.
```

---

## Phase 4: Diff

```diff
diff --git a/args.go b/args.go
index a5c7c3e..b1d2f8e 100644
--- a/args.go
+++ b/args.go
@@ -52,6 +52,19 @@ func OnlyValidArgs(cmd *Command, args []string) error {
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
@@ -155,3 +155,35 @@ func TestOnlyValidArgs(t *testing.T) {
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
+		{
+			name:    "no args",
+			args:    nil,
+			wantErr: false,
+		},
+		{
+			name:    "all unique args",
+			args:    []string{"foo", "bar", "baz"},
+			wantErr: false,
+		},
+		{
+			name:    "duplicate arg",
+			args:    []string{"foo", "bar", "foo"},
+			wantErr: true,
+		},
+		{
+			name:    "all duplicates",
+			args:    []string{"foo", "foo"},
+			wantErr: true,
+		},
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

---

## PR Summary

**PR Title:** `args: add NoDuplicateArgs positional argument validator`

**PR Body:**

## What

Adds a new `NoDuplicateArgs` positional argument validator that returns an error when the same argument value appears more than once. This complements the existing `OnlyValidArgs` validator for commands that accept a fixed set of values but should not allow repetition.

## Why

Users building CLIs with commands that accept a bounded set of positional arguments (e.g., resource names) have no built-in way to prevent duplicates. `NoDuplicateArgs` fills this gap cleanly, following the same pattern as existing validators.

## Testing

- Added table-driven tests in `args_test.go` covering: no args, all unique args, one duplicate, multiple duplicates.
- `go test ./...` passes.

Closes #2396

---

**Test Results:**
```
ok  	github.com/spf13/cobra	0.124s
```
