"""Go toolchain utilities: binary detection, test running, worktree management."""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Go binary detection
# ---------------------------------------------------------------------------

_GO_BINARY: Optional[str] = None
_DOCKER_BINARY: Optional[str] = None


def find_go() -> Optional[str]:
    global _GO_BINARY
    if _GO_BINARY:
        return _GO_BINARY

    candidates = [
        shutil.which("go"),
        "/usr/local/go/bin/go",
        "/opt/homebrew/bin/go",
        str(Path.home() / "go/bin/go"),
        "/usr/bin/go",
        "/snap/bin/go",
    ]
    for p in candidates:
        if p and Path(p).is_file():
            _GO_BINARY = p
            return p
    return None


def find_docker() -> Optional[str]:
    """Return path to docker binary if daemon is reachable, else None."""
    global _DOCKER_BINARY
    if _DOCKER_BINARY is not None:
        return _DOCKER_BINARY or None

    docker = shutil.which("docker")
    if docker:
        probe = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=8,
        )
        if probe.returncode == 0:
            _DOCKER_BINARY = docker
            return docker

    _DOCKER_BINARY = ""  # negative-cache: don't probe again
    return None


def _go_docker_image(repo_path: str) -> str:
    """Derive the golang Docker image tag from go.mod, e.g. golang:1.21."""
    go_mod = Path(repo_path) / "go.mod"
    if go_mod.exists():
        m = re.search(r"^go\s+(\d+\.\d+)", go_mod.read_text(), re.MULTILINE)
        if m:
            return f"golang:{m.group(1)}"
    return "golang:latest"


def go_available() -> bool:
    return find_go() is not None or find_docker() is not None


def run_go(args: list[str], cwd: str, timeout: int = 180) -> tuple[int, str, str]:
    """Run a go command via local binary or Docker fallback."""
    go = find_go()
    if go:
        result = subprocess.run(
            [go] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PATH": f"{Path(go).parent}:{os.environ.get('PATH', '')}"},
        )
        return result.returncode, result.stdout, result.stderr

    # Fallback: run inside the official golang Docker image
    docker = find_docker()
    if docker:
        abs_cwd = str(Path(cwd).resolve())
        image = _go_docker_image(abs_cwd)
        result = subprocess.run(
            [
                docker, "run", "--rm",
                "-v", f"{abs_cwd}:/app",
                "-w", "/app",
                image,
                "go",
            ] + args,
            capture_output=True,
            text=True,
            timeout=max(timeout, 300),  # Docker pulls may need extra time
        )
        return result.returncode, result.stdout, result.stderr

    return -1, "", "go binary not found and Docker is unavailable"


def go_build(repo_path: str) -> tuple[bool, str]:
    """Run go build ./... Returns (success, output)."""
    rc, stdout, stderr = run_go(["build", "./..."], cwd=repo_path)
    output = (stdout + "\n" + stderr).strip()
    return rc == 0, output


def go_test(repo_path: str, run_pattern: str = "") -> tuple[bool, str]:
    """Run go test ./... Returns (success, output)."""
    args = ["test", "-v", "./..."]
    if run_pattern:
        args += ["-run", run_pattern]
    rc, stdout, stderr = run_go(args, cwd=repo_path, timeout=300)
    output = (stdout + "\n" + stderr).strip()
    return rc == 0, output


def go_vet(repo_path: str) -> tuple[bool, str]:
    """Run go vet ./... Returns (success, output)."""
    rc, stdout, stderr = run_go(["vet", "./..."], cwd=repo_path)
    output = (stdout + "\n" + stderr).strip()
    return rc == 0, output


# ---------------------------------------------------------------------------
# Git worktree management for parallel patch candidates
# ---------------------------------------------------------------------------

class Worktree:
    """A temporary git worktree for isolated patch generation."""

    def __init__(self, repo_path: str, branch: str):
        self.repo_path = repo_path
        self.branch = branch
        self.path: Optional[str] = None

    def __enter__(self) -> str:
        tmp = tempfile.mkdtemp(prefix="go-agent-worktree-")
        subprocess.run(
            ["git", "worktree", "add", "--detach", tmp],
            cwd=self.repo_path,
            check=True,
            capture_output=True,
        )
        self.path = tmp
        return tmp

    def __exit__(self, *_):
        if self.path:
            subprocess.run(
                ["git", "worktree", "remove", "--force", self.path],
                cwd=self.repo_path,
                capture_output=True,
            )
            shutil.rmtree(self.path, ignore_errors=True)
            self.path = None


def get_diff(repo_path: str, base: str = "HEAD") -> str:
    """Get the current diff against base. Preserves trailing newline for git apply."""
    result = subprocess.run(
        ["git", "diff", base],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    diff = result.stdout
    if not diff.strip():
        # Check staged changes too
        result2 = subprocess.run(
            ["git", "diff", "--cached", base],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        diff = result2.stdout
    return diff if diff.strip() else "(no changes)"
