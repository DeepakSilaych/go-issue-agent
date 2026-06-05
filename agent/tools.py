"""
The agent's tools (its ACI — agent-computer interface).

`ToolExecutor` holds the actual implementations, scoped to one repo/worktree path.
`make_tools(repo_path)` wraps those implementations as LangChain `@tool` functions so
they can be handed to `create_react_agent`. Tools are deliberately built for an LLM:
exact-match edits, unique-match enforcement, informative error messages, and a command
whitelist.
"""

import shlex
import subprocess
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from .go_utils import find_docker, find_go, _go_docker_image


class ToolExecutor:
    """Implements the agent's tools, scoped to a single repo/worktree path."""

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

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

    def _tool_replace_lines(self, path: str, start_line: int, end_line: int, new_content: str) -> str:
        full = self.repo_path / path
        if not full.exists():
            return f"Error: '{path}' does not exist"
        lines = full.read_text(encoding="utf-8").splitlines(keepends=True)
        total = len(lines)
        if start_line < 1 or end_line > total or start_line > end_line:
            return (f"Error: invalid range {start_line}-{end_line} for '{path}' ({total} lines). "
                    "Use read_file to get current line numbers.")
        replacement = new_content if new_content.endswith("\n") or not new_content else new_content + "\n"
        new_lines = lines[: start_line - 1] + [replacement] + lines[end_line:]
        full.write_text("".join(new_lines), encoding="utf-8")
        return f"Replaced lines {start_line}-{end_line} in '{path}'."

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


def _safe(fn, tool_name: str, **kwargs) -> str:
    """
    Run a tool implementation, converting any exception into an error string.

    Critical for the agent loop: a tool that raises (e.g. a missing `gofmt`/`go`
    binary, a grep timeout, a binary file) must report the error back to the model
    so it can recover — never crash the whole ReAct loop.
    """
    try:
        return fn(**kwargs)
    except Exception as e:  # noqa: BLE001 — deliberately broad; surfaced to the model
        return f"Error executing {tool_name}: {type(e).__name__}: {e}"


def make_tools(repo_path: str) -> list:
    """
    Build the LangChain tool list bound to `repo_path` (a repo or worktree).

    Each tool closes over a ToolExecutor scoped to that path, so parallel patch
    candidates running in different worktrees get isolated tool sets.
    """
    ex = ToolExecutor(repo_path)

    @tool
    def list_directory(path: str = ".") -> str:
        """List files and directories at a path in the repository. Use '.' for the root."""
        return _safe(ex._tool_list_directory, "list_directory", path=path)

    @tool
    def read_file(path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
        """Read a file's contents (optionally a 1-indexed inclusive line range). Read before editing."""
        return _safe(ex._tool_read_file, "read_file", path=path, start_line=start_line, end_line=end_line)

    @tool
    def search_code(pattern: str, path: str = ".", case_sensitive: bool = True) -> str:
        """Grep for a pattern across Go source files. Returns file:line:match results."""
        return _safe(ex._tool_search_code, "search_code", pattern=pattern, path=path, case_sensitive=case_sensitive)

    @tool
    def edit_file(path: str, old_content: str, new_content: str) -> str:
        """Replace an exact, unique block of file content with new content. old_content must match exactly."""
        return _safe(ex._tool_edit_file, "edit_file", path=path, old_content=old_content, new_content=new_content)

    @tool
    def replace_lines(path: str, start_line: int, end_line: int, new_content: str) -> str:
        """Replace an inclusive 1-indexed line range with new_content. Use this when edit_file's
        exact match fails — e.g. editing inside a large embedded string literal / template.
        Read the file first to get current line numbers."""
        return _safe(ex._tool_replace_lines, "replace_lines",
                     path=path, start_line=start_line, end_line=end_line, new_content=new_content)

    @tool
    def create_file(path: str, content: str) -> str:
        """Create a new file with the given content. Fails if the file already exists."""
        return _safe(ex._tool_create_file, "create_file", path=path, content=content)

    @tool
    def run_command(command: str, timeout: int = 120) -> str:
        """Run a whitelisted command (go build/test/vet, gofmt, git, golangci-lint) in the repo."""
        return _safe(ex._tool_run_command, "run_command", command=command, timeout=timeout)

    return [list_directory, read_file, search_code, edit_file, replace_lines,
            create_file, run_command]
