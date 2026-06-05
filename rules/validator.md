# Project Rules: go-playground/validator

## Repository Overview
A struct and field validation library. Core types: `Validate`, `FieldError`, `ValidationErrors`.

## File Structure
- Root package — `validator.go`, `baked_in.go` (all built-in validators), `util.go`
- `_examples/` — usage examples (not part of library)

## Code Conventions

### Adding a Validator
1. Add the validation function in `baked_in.go` matching the `Func` type: `func(fl FieldLevel) bool`.
2. Register it in the `bakedInValidators` map in `baked_in.go`.
3. Add documentation in `doc.go`.
4. Add tests in `validator_test.go` using the existing test patterns.

### Testing Style
- Uses `testify/assert` and `testify/require`.
- Table-driven tests for validator functions.

### Naming
- Validator tag names use lowercase with underscores: `min`, `max`, `required_with`.
- Validator functions: `hasValue`, `isEmail`, `isURL`.

## What to Avoid
- Do not break backwards compatibility on existing validator tags.
- Do not add validators that duplicate existing stdlib functionality without added value.
