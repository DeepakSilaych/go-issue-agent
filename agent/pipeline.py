"""
4-phase agentic pipeline:
  1. Localize  — identify relevant files from repo map + issue
  2. Plan      — generate a precise fix strategy
  3. Patch     — implement the fix via tool-use loop
  4. Validate  — run tests, generate PR summary
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import anthropic

from .github_client import Issue, clone_or_update_repo, create_fix_branch
from .repomap import build_repo_map
from .tools import TOOL_DEFINITIONS, ToolExecutor

MODEL = "claude-sonnet-4-6"
MAX_PATCH_TURNS = 30  # safety limit on the tool-use loop


def _load_prompt(name: str) -> str:
    prompts_dir = Path(__file__).parent.parent / "prompts"
    return (prompts_dir / f"{name}.md").read_text(encoding="utf-8")


def _load_rules(repo: str) -> str:
    rules_dir = Path(__file__).parent.parent / "rules"
    repo_name = repo.split("/")[-1]
    rules_file = rules_dir / f"{repo_name}.md"
    if rules_file.exists():
        return rules_file.read_text(encoding="utf-8")
    return "(No project-specific rules available.)"


def _load_system_prompt() -> str:
    return _load_prompt("system")


class Pipeline:
    def __init__(self, repo: str, clone_dir: str, output_dir: str):
        self.repo = repo
        self.clone_dir = clone_dir
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.client = anthropic.Anthropic()
        self.repo_path: Optional[str] = None
        self.executor: Optional[ToolExecutor] = None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, issue: Issue) -> dict:
        print(f"\n{'='*60}")
        print(f"Issue #{issue.number}: {issue.title}")
        print(f"Repo: {issue.repo}")
        print(f"{'='*60}\n")

        # Setup
        print("[setup] Cloning/updating repository...")
        self.repo_path = clone_or_update_repo(issue.repo, self.clone_dir)
        self.executor = ToolExecutor(self.repo_path)
        branch = create_fix_branch(self.repo_path, issue.number)
        print(f"[setup] Working on branch: {branch}")

        print("[setup] Building repository map...")
        repo_map = build_repo_map(self.repo_path)

        project_rules = _load_rules(issue.repo)
        system_prompt = _load_system_prompt()

        # Phase 1: Localize
        print("\n[phase 1/4] Localizing relevant files...")
        localization = self._phase_localize(issue, repo_map)
        self._print_localization(localization)

        # Phase 2: Plan
        print("\n[phase 2/4] Planning the fix...")
        file_contents = self._load_relevant_files(localization)
        fix_plan = self._phase_plan(issue, project_rules, file_contents)
        print(f"\n{fix_plan}\n")

        # Phase 3: Patch
        print("\n[phase 3/4] Implementing the fix...")
        patch_result = self._phase_patch(issue, project_rules, fix_plan, system_prompt)
        print(f"\n  Result: {patch_result}")

        # Phase 4: Validate
        print("\n[phase 4/4] Validating and generating PR summary...")
        test_results = self._run_tests()
        diff = self._get_diff()
        pr_summary = self._phase_validate(issue, diff, test_results)

        # Output
        result = {
            "issue": {"number": issue.number, "title": issue.title, "url": issue.url},
            "branch": branch,
            "localization": localization,
            "fix_plan": fix_plan,
            "patch_result": patch_result,
            "test_results": test_results,
            "diff": diff,
            "pr_summary": pr_summary,
        }
        self._write_output(result, issue.number)
        return result

    # ------------------------------------------------------------------
    # Phase 1: Localize
    # ------------------------------------------------------------------

    def _phase_localize(self, issue: Issue, repo_map: str) -> dict:
        prompt = _load_prompt("localize").format(
            issue_title=issue.title,
            issue_url=issue.url,
            issue_body=issue.body or "(no body)",
            repo_map=repo_map,
        )

        resp = self.client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = resp.content[0].text
        # Extract JSON from the response (model may wrap it in markdown)
        json_match = _extract_json(raw)
        if json_match:
            try:
                return json.loads(json_match)
            except json.JSONDecodeError:
                pass

        # Fallback: return a minimal structure with all files
        return {
            "primary_files": [],
            "secondary_files": [],
            "test_files": [],
            "reasoning": raw,
        }

    def _print_localization(self, loc: dict):
        print(f"  Primary files:   {loc.get('primary_files', [])}")
        print(f"  Secondary files: {loc.get('secondary_files', [])}")
        print(f"  Test files:      {loc.get('test_files', [])}")
        print(f"  Reasoning: {loc.get('reasoning', '')[:200]}")

    # ------------------------------------------------------------------
    # Phase 2: Plan
    # ------------------------------------------------------------------

    def _phase_plan(self, issue: Issue, project_rules: str, file_contents: str) -> str:
        prompt = _load_prompt("plan").format(
            issue_title=issue.title,
            issue_url=issue.url,
            issue_body=issue.body or "(no body)",
            project_rules=project_rules,
            file_contents=file_contents,
        )

        resp = self.client.messages.create(
            model=MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text

    # ------------------------------------------------------------------
    # Phase 3: Patch (tool-use loop)
    # ------------------------------------------------------------------

    def _phase_patch(
        self,
        issue: Issue,
        project_rules: str,
        fix_plan: str,
        system_prompt: str,
    ) -> str:
        patch_prompt = _load_prompt("patch").format(
            issue_title=issue.title,
            issue_url=issue.url,
            issue_body=issue.body or "(no body)",
            project_rules=project_rules,
            fix_plan=fix_plan,
        )

        messages = [{"role": "user", "content": patch_prompt}]
        turns = 0

        while turns < MAX_PATCH_TURNS:
            turns += 1
            resp = self.client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=system_prompt,
                tools=TOOL_DEFINITIONS,
                messages=messages,
            )

            # Collect assistant message content
            assistant_content = resp.content
            messages.append({"role": "assistant", "content": assistant_content})

            # Check stop reason
            if resp.stop_reason == "end_turn":
                # Extract final text
                for block in assistant_content:
                    if hasattr(block, "text"):
                        text = block.text.strip()
                        if text.startswith("DONE:") or text.startswith("BLOCKED:"):
                            return text
                return "(completed)"

            if resp.stop_reason != "tool_use":
                return f"(unexpected stop reason: {resp.stop_reason})"

            # Execute tool calls
            tool_results = []
            for block in assistant_content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input
                print(f"    > {tool_name}({_summarize_input(tool_input)})")

                result = self.executor.execute(tool_name, tool_input)
                result_preview = result[:200] + "..." if len(result) > 200 else result
                print(f"      {result_preview.splitlines()[0] if result_preview else '(empty)'}")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

            messages.append({"role": "user", "content": tool_results})

        return f"(reached maximum turns limit of {MAX_PATCH_TURNS})"

    # ------------------------------------------------------------------
    # Phase 4: Validate
    # ------------------------------------------------------------------

    def _run_tests(self) -> str:
        print("  Running: go build ./...")
        build = subprocess.run(
            "go build ./...",
            shell=True,
            capture_output=True,
            text=True,
            cwd=self.repo_path,
            timeout=120,
        )
        print(f"  Build exit code: {build.returncode}")

        print("  Running: go test ./...")
        test = subprocess.run(
            "go test ./...",
            shell=True,
            capture_output=True,
            text=True,
            cwd=self.repo_path,
            timeout=180,
        )
        print(f"  Test exit code: {test.returncode}")

        parts = []
        parts.append(f"=== go build ./... (exit {build.returncode}) ===")
        if build.stdout:
            parts.append(build.stdout)
        if build.stderr:
            parts.append(build.stderr)

        parts.append(f"\n=== go test ./... (exit {test.returncode}) ===")
        if test.stdout:
            parts.append(test.stdout)
        if test.stderr:
            parts.append(test.stderr)

        return "\n".join(parts)

    def _get_diff(self) -> str:
        result = subprocess.run(
            "git diff HEAD",
            shell=True,
            capture_output=True,
            text=True,
            cwd=self.repo_path,
            timeout=30,
        )
        return result.stdout or "(no changes)"

    def _phase_validate(self, issue: Issue, diff: str, test_results: str) -> str:
        prompt = _load_prompt("validate").format(
            issue_title=issue.title,
            issue_url=issue.url,
            issue_body=issue.body or "(no body)",
            issue_number=issue.number,
            diff=diff[:8000],  # cap diff length
            test_results=test_results[:3000],
        )

        resp = self.client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_relevant_files(self, localization: dict) -> str:
        all_files = (
            localization.get("primary_files", [])
            + localization.get("secondary_files", [])
            + localization.get("test_files", [])
        )
        if not all_files:
            return "(no files identified)"

        parts = []
        for path in all_files[:8]:  # cap to avoid context overflow
            full = Path(self.repo_path) / path
            if not full.exists():
                parts.append(f"### {path}\n(file not found)")
                continue
            try:
                content = full.read_text(encoding="utf-8")
                parts.append(f"### {path}\n```go\n{content}\n```")
            except Exception as e:
                parts.append(f"### {path}\n(error reading: {e})")

        return "\n\n".join(parts)

    def _write_output(self, result: dict, issue_number: int):
        out_dir = self.output_dir / f"issue-{issue_number}"
        out_dir.mkdir(parents=True, exist_ok=True)

        # Write full JSON result
        (out_dir / "result.json").write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8"
        )

        # Write human-readable PR summary
        pr_file = out_dir / "pr_summary.md"
        pr_file.write_text(result["pr_summary"], encoding="utf-8")

        # Write the diff
        diff_file = out_dir / "changes.patch"
        diff_file.write_text(result["diff"], encoding="utf-8")

        print(f"\n[output] Results written to {out_dir}/")
        print(f"[output] PR summary: {pr_file}")
        print(f"[output] Diff: {diff_file}")
        print(f"\n{'='*60}")
        print("PR SUMMARY")
        print("="*60)
        print(result["pr_summary"])
        print("="*60)


# ------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------

def _extract_json(text: str) -> Optional[str]:
    """Extract the first JSON object from text (handles markdown code blocks)."""
    import re
    # Try ```json ... ``` block first
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    # Try raw JSON object
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if m:
        return m.group(0)
    return None


def _summarize_input(tool_input: dict) -> str:
    """One-line summary of tool call arguments for logging."""
    parts = []
    for k, v in tool_input.items():
        s = str(v)
        if len(s) > 50:
            s = s[:47] + "..."
        parts.append(f"{k}={s!r}")
    return ", ".join(parts)
