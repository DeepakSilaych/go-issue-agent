# Project Rules: golangci/golangci-lint

## Repository Overview
A fast Go linter aggregator. Complex architecture: linter runners, analyzers, config system.

## File Structure
- `pkg/lint/` — core linting logic
- `pkg/config/` — configuration types and loading
- `pkg/golinters/` — individual linter wrappers
- `cmd/` — CLI entry point

## Code Conventions

### Adding a Linter
1. Create a new file in `pkg/golinters/`.
2. Register the linter in `pkg/golinters/goanalysis/linters.go`.
3. Add configuration schema in `pkg/config/`.
4. Add integration tests in `test/testdata/`.

### Testing
- Unit tests alongside source.
- Integration tests in `test/` using golden file patterns.

## What to Avoid
- This is a complex codebase — avoid large changes.
- Performance is critical; new linters should not slow down the critical path.
- Config changes must maintain backward compatibility.
