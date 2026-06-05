"""Tool implementations and Claude tool definitions for the agentic loop."""

import shlex
import subprocess
from pathlib import Path
from typing import Optional

from .go_utils import find_docker, find_go, _go_docker_image

# Claude tool schema definitions
TOOL_DEFINITIONS = [
    {
        "name": "list_directory",
        "description": "List files and directories at a given path in the repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to repo root. Use '.' for root."
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file. Optionally specify line range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to repo root."
                },
                "start_line": {
                    "type": "integer",
                    "description": "First line to read (1-indexed, inclusive)."
                },
                "end_line": {
                    "type": "integer",
                    "description": "Last line to read (1-indexed, inclusive)."
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "search_code",
        "description": "Search for a pattern in Go source files using grep. Returns file:line:match results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Grep pattern to search for."
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search (relative to repo root). Defaults to '.'."
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Whether search is case-sensitive. Default true."
                }
            },
            "required": ["pattern"]
        }
    },
    {
        "name": "edit_file",
        "description": (
            "Edit a file by replacing an exact block of content with new content. "
            "The old_content must match exactly (including whitespace). "
            "Use read_file first to get the exact content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to repo root."
                },
                "old_content": {
                    "type": "string",
                    "description": "Exact content to replace. Must match exactly."
                },
                "new_content": {
                    "type": "string",
                    "description": "New content to insert in place of old_content."
                }
            },
            "required": ["path", "old_content", "new_content"]
        }
    },
    {
        "name": "create_file",
        "description": "Create a new file with given content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to repo root."
                },
                "content": {
                    "type": "string",
                    "description": "File content to write."
                }
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "run_command",
        "description": (
            "Run a go or git command in the repository directory. "
            "Allowed: go build, go test, go vet, gofmt, git diff, git status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run (must start with go, git, or gofmt)."
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds. Default 120."
                }
            },
            "required": ["command"]
        }
    }
]


class ToolExecutor:
    """Executes tool calls made by the Claude agent."""

    ALLOWED_COMMAND_PREFIXES = ("go ", "git ", "gofmt", "golangci-lint")

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    def execute(self, tool_name: str, tool_input: dict) -> str:
        handler = getattr(self, f"_tool_{tool_name}", None)
        if handler is None:
            return f"Error: Unknown tool '{tool_name}'"
        try:
            return handler(**tool_input)
        except TypeError as e:
            return f"Error: Invalid arguments for {tool_name}: {e}"
        except Exception as e:
            return f"Error executing {tool_name}: {type(e).__name__}: {e}"

    def _tool_list_directory(self, path: str = ".") -> str:
        full = self.repo_path / path
        if not full.exists():
            return f"Error: '{path}' does not exist"
        if not full.is_dir():
            return f"Error: '{path}' is not a directory"

        entries = []
        for item in sorted(full.iterdir()):
            name = item.name
            if name.startswith('.') or name in ('vendor', 'testdata', 'node_modules'):
                continue
            rel = item.relative_to(self.repo_path)
            entries.append(f"{'  ' + str(rel) + '/':50s}" if item.is_dir() else f"  {rel}")

        return "\n".join(entries) if entries else "(empty)"

    def _tool_read_file(
        self,
        path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> str:
        full = self.repo_path / path
        if not full.exists():
            return f"Error: '{path}' does not exist"
        if not full.is_file():
            return f"Error: '{path}' is not a file"

        try:
            content = full.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"Error: '{path}' is a binary file"

        lines = content.splitlines(keepends=True)
        total = len(lines)

        if start_line is not None or end_line is not None:
            s = max(0, (start_line or 1) - 1)
            e = min(total, end_line or total)
            chunk = lines[s:e]
            header = f"[{path} lines {s+1}-{e} of {total}]\n"
            numbered = [f"{s+i+1:4d}  {l}" for i, l in enumerate(chunk)]
            return header + "".join(numbered)

        numbered = [f"{i+1:4d}  {l}" for i, l in enumerate(lines)]
        return f"[{path}  ({total} lines)]\n" + "".join(numbered)

    def _tool_search_code(
        self,
        pattern: str,
        path: str = ".",
        case_sensitive: bool = True,
    ) -> str:
        full = self.repo_path / path
        flags = ["-rn", "--include=*.go"]
        if not case_sensitive:
            flags.append("-i")

        result = subprocess.run(
            ["grep", *flags, pattern, str(full)],
            capture_output=True,
            text=True,
            timeout=30,
        )

        raw = result.stdout.strip()
        if not raw:
            return f"No matches found for '{pattern}'"

        lines = []
        for line in raw.splitlines()[:100]:  # cap at 100 matches
            try:
                abs_path, rest = line.split(":", 1)
                rel = Path(abs_path).relative_to(self.repo_path)
                lines.append(f"{rel}:{rest}")
            except (ValueError, OSError):
                lines.append(line)

        suffix = f"\n... (showing first 100 of more results)" if len(raw.splitlines()) > 100 else ""
        return "\n".join(lines) + suffix

    def _tool_edit_file(self, path: str, old_content: str, new_content: str) -> str:
        full = self.repo_path / path
        if not full.exists():
            return f"Error: '{path}' does not exist"

        current = full.read_text(encoding="utf-8")

        if old_content not in current:
            # Give a hint about how close we are
            stripped_old = old_content.strip()
            if stripped_old in current:
                return (
                    "Error: Content not found with exact whitespace. "
                    "Try matching the indentation exactly as shown by read_file."
                )
            return (
                f"Error: Could not find the specified old_content in '{path}'. "
                "Use read_file to get the exact content, including all whitespace."
            )

        count = current.count(old_content)
        if count > 1:
            return (
                f"Error: old_content appears {count} times in '{path}'. "
                "Provide more surrounding context to make the match unique."
            )

        full.write_text(current.replace(old_content, new_content, 1), encoding="utf-8")
        return f"Edited '{path}' successfully."

    def _tool_create_file(self, path: str, content: str) -> str:
        full = self.repo_path / path
        if full.exists():
            return f"Error: '{path}' already exists. Use edit_file to modify it."
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return f"Created '{path}' successfully."

    def _tool_run_command(self, command: str, timeout: int = 120) -> str:
        try:
            tokens = shlex.split(command.strip())
        except ValueError as e:
            return f"Error: could not parse command: {e}"

        if not tokens:
            return "Error: empty command"

        # Security: validate first token against whitelist (no shell=True)
        allowed_bins = {"go", "git", "gofmt", "golangci-lint"}
        if tokens[0] not in allowed_bins:
            return (
                f"Error: Command not allowed: {tokens[0]!r}. "
                f"Only these commands are permitted: {sorted(allowed_bins)}"
            )

        # Resolve `go` to local binary or Docker
        if tokens[0] == "go":
            go_bin = find_go()
            if go_bin:
                tokens[0] = go_bin
                result = subprocess.run(
                    tokens,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=str(self.repo_path),
                )
            else:
                docker = find_docker()
                if not docker:
                    return "stderr:\ngo binary not found and Docker is unavailable\n\nexit_code: 1"
                abs_path = str(self.repo_path.resolve())
                image = _go_docker_image(abs_path)
                result = subprocess.run(
                    [docker, "run", "--rm",
                     "-v", f"{abs_path}:/app",
                     "-w", "/app",
                     image] + tokens,
                    capture_output=True,
                    text=True,
                    timeout=max(timeout, 300),
                )
        else:
            result = subprocess.run(
                tokens,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.repo_path),
            )

        parts = []
        if result.stdout:
            parts.append(f"stdout:\n{result.stdout.rstrip()}")
        if result.stderr:
            parts.append(f"stderr:\n{result.stderr.rstrip()}")
        parts.append(f"exit_code: {result.returncode}")
        return "\n\n".join(parts)
