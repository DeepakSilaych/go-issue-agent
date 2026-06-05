"""
4-phase agentic pipeline:
  1. Localize  — identify relevant files from repo map + issue
  2. Plan      — generate a precise fix strategy
  3. Patch     — implement the fix via tool-use loop
  4. Validate  — run tests, generate PR summary
"""

import json
import subprocess
from pathlib import Path
from typing import Optional

from .github_client import Issue, clone_or_update_repo, create_fix_branch
from .llm_client import make_client
from .repomap import build_repo_map
from .tools import TOOL_DEFINITIONS, ToolExecutor

MAX_PATCH_TURNS = 30


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


class Pipeline:
    def __init__(self, repo: str, clone_dir: str, output_dir: str, provider: str, model: str):
        self.repo = repo
        self.clone_dir = clone_dir
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.llm = make_client(provider, model)
        self.repo_path: Optional[str] = None
        self.executor: Optional[ToolExecutor] = None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, issue: Issue) -> dict:
        print(f"\n{'='*60}")
        print(f"Issue #{issue.number}: {issue.title}")
        print(f"Repo: {issue.repo}  |  Provider: {self.llm.__class__.__name__}  |  Model: {self.llm.model}")
        print(f"{'='*60}\n")

        print("[setup] Cloning/updating repository...")
        self.repo_path = clone_or_update_repo(issue.repo, self.clone_dir)
        self.executor = ToolExecutor(self.repo_path)
        branch = create_fix_branch(self.repo_path, issue.number)
        print(f"[setup] Working on branch: {branch}")

        print("[setup] Building repository map...")
        repo_map = build_repo_map(self.repo_path)

        project_rules = _load_rules(issue.repo)
        system_prompt = _load_prompt("system")

        print("\n[phase 1/4] Localizing relevant files...")
        localization = self._phase_localize(issue, repo_map)
        self._print_localization(localization)

        print("\n[phase 2/4] Planning the fix...")
        file_contents = self._load_relevant_files(localization)
        fix_plan = self._phase_plan(issue, project_rules, file_contents)
        print(f"\n{fix_plan}\n")

        print("\n[phase 3/4] Implementing the fix...")
        patch_result = self._phase_patch(issue, project_rules, fix_plan, system_prompt)
        print(f"\n  Result: {patch_result}")

        print("\n[phase 4/4] Validating and generating PR summary...")
        test_results = self._run_tests()
        diff = self._get_diff()
        pr_summary = self._phase_validate(issue, diff, test_results)

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
        result = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
        )
        json_str = _extract_json(result.text)
        if json_str:
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass
        return {"primary_files": [], "secondary_files": [], "test_files": [], "reasoning": result.text}

    def _print_localization(self, loc: dict):
        print(f"  Primary:   {loc.get('primary_files', [])}")
        print(f"  Secondary: {loc.get('secondary_files', [])}")
        print(f"  Tests:     {loc.get('test_files', [])}")
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
        result = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
        )
        return result.text

    # ------------------------------------------------------------------
    # Phase 3: Patch (tool-use loop)
    # ------------------------------------------------------------------

    def _phase_patch(self, issue: Issue, project_rules: str, fix_plan: str, system_prompt: str) -> str:
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
            result = self.llm.chat(
                messages=messages,
                system=system_prompt,
                tools=TOOL_DEFINITIONS,
                max_tokens=4096,
            )

            # Append assistant turn to history
            messages.append(self.llm.assistant_message(result))

            if result.stop_reason == "end_turn":
                text = result.text.strip()
                if text.startswith("DONE:") or text.startswith("BLOCKED:"):
                    return text
                return text or "(completed)"

            if not result.tool_calls:
                return f"(unexpected stop with no tool calls: {result.stop_reason})"

            # Execute each tool call
            tool_results = []
            for tc in result.tool_calls:
                print(f"    > {tc.name}({_summarize_input(tc.input)})")
                output = self.executor.execute(tc.name, tc.input)
                preview = output.splitlines()[0][:120] if output else "(empty)"
                print(f"      {preview}")
                tool_results.append(output)

            # Append tool results (provider-specific format)
            tool_msgs = self.llm.tool_result_message(result.tool_calls, tool_results)
            if isinstance(tool_msgs, list):
                messages.extend(tool_msgs)
            else:
                messages.append(tool_msgs)

        return f"(reached turn limit of {MAX_PATCH_TURNS})"

    # ------------------------------------------------------------------
    # Phase 4: Validate
    # ------------------------------------------------------------------

    def _run_tests(self) -> str:
        print("  Running: go build ./...")
        build = subprocess.run("go build ./...", shell=True, capture_output=True, text=True,
                               cwd=self.repo_path, timeout=120)
        print(f"  Build exit: {build.returncode}")

        print("  Running: go test ./...")
        test = subprocess.run("go test ./...", shell=True, capture_output=True, text=True,
                              cwd=self.repo_path, timeout=180)
        print(f"  Test exit:  {test.returncode}")

        parts = [
            f"=== go build ./... (exit {build.returncode}) ===",
            build.stdout or "", build.stderr or "",
            f"\n=== go test ./... (exit {test.returncode}) ===",
            test.stdout or "", test.stderr or "",
        ]
        return "\n".join(parts)

    def _get_diff(self) -> str:
        r = subprocess.run("git diff HEAD", shell=True, capture_output=True, text=True,
                           cwd=self.repo_path, timeout=30)
        return r.stdout or "(no changes)"

    def _phase_validate(self, issue: Issue, diff: str, test_results: str) -> str:
        prompt = _load_prompt("validate").format(
            issue_title=issue.title,
            issue_url=issue.url,
            issue_body=issue.body or "(no body)",
            issue_number=issue.number,
            diff=diff[:8000],
            test_results=test_results[:3000],
        )
        result = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
        )
        return result.text

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
        for path in all_files[:8]:
            full = Path(self.repo_path) / path
            if not full.exists():
                parts.append(f"### {path}\n(file not found)")
                continue
            try:
                content = full.read_text(encoding="utf-8")
                parts.append(f"### {path}\n```go\n{content}\n```")
            except Exception as e:
                parts.append(f"### {path}\n(error: {e})")
        return "\n\n".join(parts)

    def _write_output(self, result: dict, issue_number: int):
        out_dir = self.output_dir / f"issue-{issue_number}"
        out_dir.mkdir(parents=True, exist_ok=True)

        (out_dir / "result.json").write_text(json.dumps(result, indent=2, default=str))
        (out_dir / "pr_summary.md").write_text(result["pr_summary"])
        (out_dir / "changes.patch").write_text(result["diff"])

        print(f"\n[output] Written to {out_dir}/")
        print(f"\n{'='*60}\nPR SUMMARY\n{'='*60}")
        print(result["pr_summary"])
        print("="*60)


# ------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------

def _extract_json(text: str) -> Optional[str]:
    import re
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if m:
        return m.group(0)
    return None


def _summarize_input(tool_input: dict) -> str:
    parts = []
    for k, v in tool_input.items():
        s = str(v)
        if len(s) > 50:
            s = s[:47] + "..."
        parts.append(f"{k}={s!r}")
    return ", ".join(parts)
