"""Build a lightweight map of the repository for context injection."""

import re
import subprocess
from pathlib import Path


def build_repo_map(repo_path: str, max_files: int = 80) -> str:
    """
    Build a concise repository map: directory tree + top-level Go symbols.

    The map is designed to fit in ~8k tokens so the model can reason about
    the whole codebase structure before deciding which files to read in full.
    """
    root = Path(repo_path).resolve()

    go_files = _collect_go_files(root, max_files)
    if not go_files:
        return "(no Go files found)"

    # Group files by directory
    dirs: dict[str, list[Path]] = {}
    for f in go_files:
        rel = f.relative_to(root)
        d = str(rel.parent)
        dirs.setdefault(d, []).append(f)

    sections = ["# Repository Map\n"]
    sections.append(_build_tree(root))
    sections.append("\n## Symbol Index\n")

    for d in sorted(dirs.keys()):
        files = sorted(dirs[d])
        sections.append(f"\n### Package: {d or '(root)'}\n")
        for f in files:
            rel = f.relative_to(root)
            symbols = _extract_symbols(f)
            if symbols:
                sections.append(f"\n**{rel}**\n{symbols}")

    return "\n".join(sections)


def _collect_go_files(root: Path, max_files: int) -> list[Path]:
    excluded_dirs = {"vendor", "testdata", ".git", "node_modules", "_test"}
    files = []
    for f in sorted(root.rglob("*.go")):
        parts = set(f.relative_to(root).parts[:-1])
        if parts & excluded_dirs:
            continue
        files.append(f)
        if len(files) >= max_files:
            break
    return files


def _build_tree(root: Path) -> str:
    """Build a compact directory tree (dirs only)."""
    lines = ["## Directory Tree\n```"]
    result = subprocess.run(
        ["find", ".", "-type", "d",
         "-not", "-path", "*/vendor/*",
         "-not", "-path", "*/.git/*",
         "-not", "-path", "*/testdata/*",
         "-not", "-path", "*/node_modules/*"],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=10,
    )
    for line in sorted(result.stdout.splitlines()):
        if line != ".":
            lines.append(line)
    lines.append("```")
    return "\n".join(lines)


def _extract_symbols(filepath: Path) -> str:
    """Extract top-level declarations from a Go file."""
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return ""

    symbols = []
    lines = content.splitlines()

    # Patterns for top-level Go declarations
    patterns = [
        (re.compile(r"^func\s+(\w+)"), "func"),
        (re.compile(r"^func\s+\(.*?\)\s+(\w+)"), "method"),
        (re.compile(r"^type\s+(\w+)"), "type"),
        (re.compile(r"^var\s+(\w+)"), "var"),
        (re.compile(r"^const\s+(\w+)"), "const"),
    ]

    for i, line in enumerate(lines, 1):
        if line.startswith("//") or not line.strip():
            continue
        for pat, kind in patterns:
            if pat.match(line):
                # Trim long signatures
                sig = line.rstrip()
                if len(sig) > 100:
                    sig = sig[:100] + " ..."
                symbols.append(f"  L{i:4d}  {sig}")
                break

    return "\n".join(symbols)
