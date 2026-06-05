# Project Rules: spf13/cobra

## Repository Overview
Cobra is a CLI framework for Go. The core types are `Command`, `Flag`, and `Args`.

## File Structure
- Root package (`*.go`) — core `Command`, flag handling, completions, args validation
- `doc/` — documentation generators (man pages, markdown, rst, yaml)
- `*_test.go` — tests live alongside source files

## Code Conventions

### Error Handling
- Functions return `error` as the last return value.
- Sentinel errors: `ErrSubCommandRequired`, `ErrCommandMissing`, etc.
- Use `fmt.Errorf("cobra: %w", err)` style wrapping when adding context.
- `RunE` functions return errors; `Run` functions do not.

### Testing Style
- Table-driven tests using `t.Run` subtests.
- Test helpers use `t.Helper()` at the top.
- Command tests typically create a root command with `&cobra.Command{Use: "root"}`.
- Assert with standard `t.Errorf` / `t.Fatalf`, not external assertion libraries.
- Test files end in `_test.go` and are in the same package (not `_test` suffix package).

### Naming
- Exported types: `Command`, `PositionalArgs`, `FParseErrWhitelist`.
- Internal helpers: lowercase, concise (`validateArgs`, `matchesBashCompletionFile`).

### Formatting
- Standard `gofmt` formatting is required.
- No trailing spaces. Tabs for indentation.
- Receiver names are short (1-2 chars): `c *Command`.

### Completions
- Shell completion files are in the root package (`completions.go`, `bash_completions.go`, etc.).
- Completion annotations use the `BashCompCustom` and `ShellCompDirective` types.

## Common Patterns

```go
// A typical command definition
var myCmd = &cobra.Command{
    Use:   "mycommand [flags]",
    Short: "Brief description",
    Long:  `Longer description.`,
    RunE: func(cmd *cobra.Command, args []string) error {
        // implementation
        return nil
    },
}

// A typical table-driven test
func TestMyFeature(t *testing.T) {
    tests := []struct {
        name    string
        input   string
        want    string
        wantErr bool
    }{
        {"basic", "input", "expected", false},
    }
    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            // test body
        })
    }
}
```

## What to Avoid
- Do not add external dependencies; cobra has none.
- Do not use `os.Exit` in library code; return errors instead.
- Do not modify `go.mod` or `go.sum` unless absolutely necessary.
- Do not add global variables unless following existing patterns.
