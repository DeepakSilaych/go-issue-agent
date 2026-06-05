# Project Rules: gin-gonic/gin

## Repository Overview
Gin is an HTTP web framework for Go. Core types: `Engine`, `RouterGroup`, `Context`, `HandlerFunc`.

## File Structure
- Root package — `gin.go`, `context.go`, `router.go`, `tree.go`, `middleware.go`
- `binding/` — request body binding
- `render/` — response rendering
- `internal/` — internal utilities

## Code Conventions

### Error Handling
- `Context.AbortWithError(code, err)` for request-scoped errors.
- `Engine.Use()` registers middleware that can short-circuit with `c.Abort()`.

### Testing Style
- Table-driven tests with `t.Run`.
- Use `httptest.NewRecorder()` and `httptest.NewRequest()` for HTTP tests.
- `testify/assert` is used extensively for assertions.

### Naming
- Receiver: `c *Context`, `r *RouterGroup`, `engine *Engine`.
- Middleware functions return `HandlerFunc`.

### Formatting
- Standard `gofmt`. No external linter beyond the CI checks.

## What to Avoid
- Do not add dependencies beyond what is in `go.mod`.
- Do not change routing algorithm in `tree.go` without deep understanding.
- Performance-sensitive paths (hot path in routing) need benchmarks.
