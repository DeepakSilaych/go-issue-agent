"""
SOTA 5-phase pipeline:
  1. Retrieve   — BM25 symbol search + similar PR examples
  2. Localize   — hierarchical: file → function → exact lines
  3. Plan       — targeted fix with PR-style context
  4. Multi-Patch — 3 parallel candidates via git worktrees, test-ranked
  5. Validate   — self-heal loop + PR summary
"""

import json
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from .github_client import Issue, clone_or_update_repo, create_fix_branch
from .go_utils import Worktree, find_docker, find_go, get_diff, go_available, go_build, go_test, go_vet
from .indexer import build_symbol_index, build_test_helpers, index_exists, load_index
from .llm_client import make_client
from .repomap import build_repo_map
from .retrieval import (
    format_prs_for_prompt,
    format_symbols_for_prompt,
    retrieve_similar_prs,
    retrieve_symbols,
)
from .tools import TOOL_DEFINITIONS, ToolExecutor

MAX_PATCH_TURNS = 30
MAX_HEAL_ROUNDS = 3
NUM_CANDIDATES = 3

# Per-candidate strategy nudges — each tries a slightly different approach
CANDIDATE_STRATEGIES = [
    "Follow the fix plan closely. Prefer minimal changes — only touch what is necessary.",
    "Focus on correctness and edge cases. Add more test coverage than the minimal plan suggests.",
    "Follow the project's existing patterns as closely as possible. Find the most similar existing function and model your implementation after it.",
]


def _load_prompt(name: str) -> str:
    return (Path(__file__).parent.parent / "prompts" / f"{name}.md").read_text()


def _load_rules(repo: str) -> str:
    rules_file = Path(__file__).parent.parent / "rules" / f"{repo.split('/')[-1]}.md"
    return rules_file.read_text() if rules_file.exists() else "(No project-specific rules.)"


class Pipeline:
    def __init__(self, repo: str, clone_dir: str, output_dir: str, provider: str, model: str):
        self.repo = repo
        self.clone_dir = clone_dir
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.llm = make_client(provider, model)
        self.repo_path: Optional[str] = None
        self.project_rules = _load_rules(repo)
        self.system_prompt = _load_prompt("system")

        go = find_go()
        docker = find_docker()
        if go:
            print(f"  Go found at: {go}")
        elif docker:
            print(f"  Go not found locally — will use Docker (golang image) for tests")
        else:
            print("  WARNING: go binary not found and Docker unavailable — test execution disabled")

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self, issue: Issue) -> dict:
        print(f"\n{'='*60}")
        print(f"Issue #{issue.number}: {issue.title}")
        print(f"Repo: {issue.repo}  Provider: {self.llm.__class__.__name__}  Model: {self.llm.model}")
        print(f"{'='*60}\n")

        # Setup repo
        print("[setup] Cloning/updating repository...")
        self.repo_path = clone_or_update_repo(issue.repo, self.clone_dir)
        branch = create_fix_branch(self.repo_path, issue.number)
        print(f"[setup] Branch: {branch}")

        # Load or build index
        print("[setup] Loading knowledge index...")
        index = self._ensure_index(issue.repo)

        # Repo map (lightweight version for localization)
        repo_map = build_repo_map(self.repo_path, max_files=60)

        # Phase 1: Retrieve
        print("\n[phase 1/5] Retrieving relevant symbols and similar PRs...")
        query = f"{issue.title} {issue.body}"
        retrieved_symbols = retrieve_symbols(query, index["symbols"], top_k=25)
        similar_prs = retrieve_similar_prs(issue.title, issue.body, index["pr_examples"], top_k=3)
        print(f"  Symbols retrieved: {len(retrieved_symbols)}")
        print(f"  Similar PRs found: {len(similar_prs)}")
        if similar_prs:
            for pr in similar_prs:
                print(f"    - #{pr.get('pr_number')}: {pr.get('pr_title', '')[:60]}")

        symbols_text = format_symbols_for_prompt(retrieved_symbols)
        prs_text = format_prs_for_prompt(similar_prs)

        # Phase 2: Deep hierarchical localization
        print("\n[phase 2/5] Deep hierarchical localization...")
        localization = self._phase_localize(issue, symbols_text, repo_map)
        self._print_localization(localization)

        # Phase 3: Plan
        print("\n[phase 3/5] Planning fix with PR context...")
        file_contents = self._load_located_files(localization)
        fix_plan = self._phase_plan(issue, file_contents, prs_text)
        print(f"\n{fix_plan[:800]}...\n" if len(fix_plan) > 800 else f"\n{fix_plan}\n")

        # Phase 4: Multi-candidate patching
        print(f"\n[phase 4/5] Generating {NUM_CANDIDATES} patch candidates in parallel...")
        edit_locations_text = self._format_edit_locations(localization)
        best_candidate = self._phase_multi_patch(issue, fix_plan, edit_locations_text, prs_text)

        # Phase 5: Validate + self-heal + PR summary
        print("\n[phase 5/5] Validating and generating PR summary...")
        final_result = self._phase_validate(issue, best_candidate)

        self._write_output(final_result, issue.number)
        return final_result

    # ------------------------------------------------------------------
    # Phase 1: Retrieve — already done inline in run()
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Phase 2: Deep hierarchical localization
    # ------------------------------------------------------------------

    def _phase_localize(self, issue: Issue, symbols_text: str, repo_map: str) -> dict:
        prompt = _load_prompt("localize_deep").format(
            issue_title=issue.title,
            issue_url=issue.url,
            issue_body=issue.body or "(no body)",
            retrieved_symbols=symbols_text,
            repo_map=repo_map,
        )
        result = self.llm.chat(messages=[{"role": "user", "content": prompt}], max_tokens=1500)
        parsed = _extract_json_obj(result.text)
        if parsed:
            return parsed
        # Fallback: use old localize prompt
        return self._phase_localize_fallback(issue, repo_map)

    def _phase_localize_fallback(self, issue: Issue, repo_map: str) -> dict:
        prompt = _load_prompt("localize").format(
            issue_title=issue.title,
            issue_url=issue.url,
            issue_body=issue.body or "(no body)",
            repo_map=repo_map,
        )
        result = self.llm.chat(messages=[{"role": "user", "content": prompt}], max_tokens=1024)
        parsed = _extract_json_obj(result.text)
        if parsed:
            # Adapt old format to new
            return {
                "edit_locations": [{"file": f, "line": 1, "symbol": "", "reason": ""} for f in parsed.get("primary_files", [])],
                "new_locations": [],
                "test_locations": [{"file": f, "line": 1, "symbol": "", "reason": ""} for f in parsed.get("test_files", [])],
                "context_files": parsed.get("secondary_files", []),
                "reasoning": parsed.get("reasoning", ""),
            }
        return {"edit_locations": [], "new_locations": [], "test_locations": [], "context_files": [], "reasoning": result.text}

    def _print_localization(self, loc: dict):
        for el in loc.get("edit_locations", []):
            print(f"  EDIT   {el.get('file')}:{el.get('line')}  {el.get('symbol', '')}")
        for nl in loc.get("new_locations", []):
            print(f"  NEW    {nl.get('file')} after L{nl.get('after_line')}  {nl.get('after_symbol', '')}")
        for tl in loc.get("test_locations", []):
            print(f"  TEST   {tl.get('file')}:{tl.get('line')}  {tl.get('symbol', '')}")
        for cf in loc.get("context_files", [])[:3]:
            print(f"  CTX    {cf}")

    # ------------------------------------------------------------------
    # Phase 3: Plan
    # ------------------------------------------------------------------

    def _phase_plan(self, issue: Issue, file_contents: str, prs_text: str) -> str:
        prompt = _load_prompt("plan").format(
            issue_title=issue.title,
            issue_url=issue.url,
            issue_body=issue.body or "(no body)",
            project_rules=self.project_rules,
            file_contents=file_contents + f"\n\n## Similar Past PRs\n{prs_text}",
        )
        result = self.llm.chat(messages=[{"role": "user", "content": prompt}], max_tokens=2048)
        return result.text

    # ------------------------------------------------------------------
    # Phase 4: Multi-candidate patching
    # ------------------------------------------------------------------

    def _phase_multi_patch(
        self, issue: Issue, fix_plan: str, edit_locations_text: str, prs_text: str
    ) -> dict:
        """Generate NUM_CANDIDATES patches in parallel, return the best."""

        results = []
        with ThreadPoolExecutor(max_workers=NUM_CANDIDATES) as pool:
            futures = {
                pool.submit(
                    self._run_candidate,
                    candidate_id=i + 1,
                    issue=issue,
                    fix_plan=fix_plan,
                    edit_locations=edit_locations_text,
                    prs_text=prs_text,
                    strategy=CANDIDATE_STRATEGIES[i],
                ): i
                for i in range(NUM_CANDIDATES)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    print(f"  Candidate {idx+1}: {result['status']}  tests={'PASS' if result['tests_pass'] else 'FAIL'}")
                except Exception as e:
                    print(f"  Candidate {idx+1}: ERROR — {e}")

        # Rank: passing tests first, then by diff size (prefer smaller diffs)
        passing = [r for r in results if r["tests_pass"]]
        if passing:
            best = min(passing, key=lambda r: len(r["diff"]))
            print(f"  Best candidate: #{best['candidate_id']} (tests pass, diff size {len(best['diff'])})")
        elif results:
            # Prefer smallest diff when tests can't validate — least invasive change
            with_changes = [r for r in results if r["diff"] != "(no changes)"]
            pool = with_changes if with_changes else results
            best = min(pool, key=lambda r: len(r["diff"]))
            print(f"  No passing candidates — using candidate #{best['candidate_id']} (smallest diff, best effort)")
        else:
            print("  All candidates failed — returning empty")
            return {"candidate_id": 0, "diff": "(no changes)", "tests_pass": False, "status": "failed", "test_output": ""}

        # Save best diff to a debug file so we can inspect it
        debug_patch = self.output_dir / "debug_candidate.patch"
        debug_patch.parent.mkdir(parents=True, exist_ok=True)
        debug_patch.write_text(best["diff"])
        print(f"  Debug: saved candidate diff to {debug_patch} ({len(best['diff'])} bytes)")

        # Apply best candidate diff to main working tree
        self._apply_candidate_diff(best["diff"])
        return best

    def _run_candidate(
        self,
        candidate_id: int,
        issue: Issue,
        fix_plan: str,
        edit_locations: str,
        prs_text: str,
        strategy: str,
    ) -> dict:
        """Run a single patch candidate in an isolated git worktree."""
        print(f"  Starting candidate {candidate_id}...")

        with Worktree(self.repo_path, f"candidate-{candidate_id}") as worktree_path:
            executor = ToolExecutor(worktree_path)

            patch_prompt = _load_prompt("patch_candidate").format(
                candidate_id=candidate_id,
                total_candidates=NUM_CANDIDATES,
                issue_title=issue.title,
                issue_body=issue.body or "(no body)",
                project_rules=self.project_rules,
                fix_plan=fix_plan,
                candidate_strategy=strategy,
                similar_prs=prs_text,
                edit_locations=edit_locations,
            )

            messages = [{"role": "user", "content": patch_prompt}]
            status = f"incomplete"
            turns = 0

            while turns < MAX_PATCH_TURNS:
                turns += 1
                result = self.llm.chat(
                    messages=messages,
                    system=self.system_prompt,
                    tools=TOOL_DEFINITIONS,
                    max_tokens=4096,
                )
                messages.append(self.llm.assistant_message(result))

                if result.stop_reason == "end_turn":
                    text = result.text.strip()
                    if text.startswith("DONE:") or text.startswith("BLOCKED:"):
                        status = text[:80]
                    break

                if not result.tool_calls:
                    break

                tool_results = []
                for tc in result.tool_calls:
                    output = executor.execute(tc.name, tc.input)
                    tool_results.append(output)
                    # Log every edit/create call so we can see if it's working
                    if tc.name in ("edit_file", "create_file"):
                        short_out = output[:80].replace('\n', ' ')
                        print(f"    [C{candidate_id}] {tc.name}({tc.input.get('path', '')}) → {short_out}")

                tool_msgs = self.llm.tool_result_message(result.tool_calls, tool_results)
                if isinstance(tool_msgs, list):
                    messages.extend(tool_msgs)
                else:
                    messages.append(tool_msgs)

            print(f"    [C{candidate_id}] Finished in {turns} turns")

            # Get diff from this worktree
            diff = get_diff(worktree_path)
            print(f"    [C{candidate_id}] Diff: {len(diff)} bytes")

            # Run tests if Go available
            tests_pass, test_output = False, ""
            if go_available() and diff != "(no changes)":
                tests_pass, test_output = go_test(worktree_path)

            return {
                "candidate_id": candidate_id,
                "diff": diff,
                "tests_pass": tests_pass,
                "test_output": test_output[:2000],
                "status": status,
            }

    def _apply_candidate_diff(self, diff: str):
        """Apply a diff to the main working tree."""
        if not diff or diff == "(no changes)":
            return
        # Validate it's a real unified diff (must have --- / +++ headers)
        if "--- " not in diff or "+++ " not in diff:
            print(f"  Warning: diff doesn't look like a unified diff ({len(diff)} bytes) — skipping apply")
            return
        # Reset main working tree before applying so we don't get conflicts
        subprocess.run(["git", "checkout", "--", "."], cwd=self.repo_path, capture_output=True)
        try:
            # --check first to validate before actually applying
            check = subprocess.run(
                ["git", "apply", "--check", "--whitespace=nowarn"],
                input=diff,
                text=True,
                cwd=self.repo_path,
                capture_output=True,
            )
            if check.returncode != 0:
                print(f"  Warning: diff pre-check failed: {check.stderr[:200]}")
                print("  Attempting 3-way merge fallback...")
                # Try with --3way which can handle some conflicts
                proc = subprocess.run(
                    ["git", "apply", "--3way", "--whitespace=nowarn"],
                    input=diff,
                    text=True,
                    cwd=self.repo_path,
                    capture_output=True,
                )
                if proc.returncode != 0:
                    print(f"  Warning: 3-way apply also failed: {proc.stderr[:200]}")
            else:
                subprocess.run(
                    ["git", "apply", "--whitespace=nowarn"],
                    input=diff,
                    text=True,
                    cwd=self.repo_path,
                    capture_output=True,
                    check=True,
                )
                print("  Applied diff successfully.")
        except Exception as e:
            print(f"  Warning: could not apply diff: {e}")

    # ------------------------------------------------------------------
    # Phase 5: Validate + self-heal + PR summary
    # ------------------------------------------------------------------

    def _phase_validate(self, issue: Issue, candidate: dict) -> dict:
        # Get final diff after applying candidate
        final_diff = get_diff(self.repo_path)

        # Run validation
        tests_pass = candidate["tests_pass"]
        test_output = candidate["test_output"]

        if go_available() and final_diff != "(no changes)":
            # Self-heal: if tests fail, try to fix up to MAX_HEAL_ROUNDS
            if not tests_pass:
                tests_pass, test_output, final_diff = self._self_heal(
                    issue, final_diff, test_output
                )

            # Final validation run
            build_ok, build_out = go_build(self.repo_path)
            tests_pass, test_output = go_test(self.repo_path)
            vet_ok, vet_out = go_vet(self.repo_path)
            print(f"  Build: {'PASS' if build_ok else 'FAIL'}")
            print(f"  Tests: {'PASS' if tests_pass else 'FAIL'}")
            print(f"  Vet:   {'PASS' if vet_ok else 'FAIL'}")
        else:
            build_ok, build_out, vet_ok, vet_out = None, "", None, ""

        # Generate PR summary
        pr_summary = self._generate_pr_summary(issue, final_diff, test_output, tests_pass)

        return {
            "issue": {"number": issue.number, "title": issue.title, "url": issue.url},
            "diff": final_diff,
            "tests_pass": tests_pass,
            "test_output": test_output,
            "build_ok": build_ok,
            "pr_summary": pr_summary,
            "candidate_id": candidate["candidate_id"],
        }

    def _self_heal(self, issue: Issue, diff: str, failing_output: str) -> tuple[bool, str, str]:
        """Feed test failures back to the agent and let it fix them."""
        executor = ToolExecutor(self.repo_path)

        heal_prompt = (
            f"The following tests are failing after your fix. "
            f"Please fix the failures using the available tools.\n\n"
            f"Issue: {issue.title}\n\n"
            f"Test output:\n```\n{failing_output[:3000]}\n```\n\n"
            "Use read_file, edit_file, and run_command to fix all failures. "
            "When all tests pass, respond: DONE: <summary>"
        )

        messages = [{"role": "user", "content": heal_prompt}]

        for round_num in range(MAX_HEAL_ROUNDS):
            print(f"  Self-heal round {round_num + 1}/{MAX_HEAL_ROUNDS}...")
            turns = 0

            while turns < 15:
                turns += 1
                result = self.llm.chat(
                    messages=messages,
                    system=self.system_prompt,
                    tools=TOOL_DEFINITIONS,
                    max_tokens=4096,
                )
                messages.append(self.llm.assistant_message(result))

                if result.stop_reason == "end_turn":
                    break

                if not result.tool_calls:
                    break

                tool_results = []
                for tc in result.tool_calls:
                    output = executor.execute(tc.name, tc.input)
                    tool_results.append(output)
                tool_msgs = self.llm.tool_result_message(result.tool_calls, tool_results)
                if isinstance(tool_msgs, list):
                    messages.extend(tool_msgs)
                else:
                    messages.append(tool_msgs)

            # Check if fixed
            tests_pass, test_output = go_test(self.repo_path)
            if tests_pass:
                print(f"  Self-heal succeeded in round {round_num + 1}")
                return True, test_output, get_diff(self.repo_path)

        return False, failing_output, diff

    def _generate_pr_summary(self, issue: Issue, diff: str, test_output: str, tests_pass: bool) -> str:
        prompt = _load_prompt("validate").format(
            issue_title=issue.title,
            issue_url=issue.url,
            issue_body=issue.body or "(no body)",
            issue_number=issue.number,
            diff=diff[:8000],
            test_results=f"Tests: {'PASS' if tests_pass else 'FAIL'}\n\n{test_output[:2000]}",
        )
        result = self.llm.chat(messages=[{"role": "user", "content": prompt}], max_tokens=1024)
        return result.text

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_index(self, repo: str) -> dict:
        """Load existing index or build a fast one on the fly."""
        if index_exists(repo):
            index = load_index(repo)
            print(f"  Loaded index: {len(index['symbols'])} symbols, {len(index['pr_examples'])} PRs")
            return index

        # Build lightweight index on the fly (no PR examples, just symbols)
        print("  No pre-built index found — building symbol index on the fly...")
        symbols = build_symbol_index(self.repo_path)
        test_helpers = build_test_helpers(self.repo_path)
        print(f"  Built {len(symbols)} symbols, {len(test_helpers)} test helpers")
        return {"symbols": symbols, "pr_examples": [], "test_helpers": test_helpers}

    def _load_located_files(self, localization: dict) -> str:
        """Load file contents for all located edit/test/context files."""
        files = set()
        for loc in localization.get("edit_locations", []):
            files.add(loc["file"])
        for loc in localization.get("test_locations", []):
            files.add(loc["file"])
        for f in localization.get("context_files", [])[:3]:
            files.add(f)

        if not files:
            return "(no files identified)"

        parts = []
        for path in sorted(files)[:8]:
            full = Path(self.repo_path) / path
            if not full.exists():
                continue
            try:
                content = full.read_text(encoding="utf-8")
                parts.append(f"### {path}\n```go\n{content}\n```")
            except Exception:
                pass
        return "\n\n".join(parts)

    def _format_edit_locations(self, localization: dict) -> str:
        lines = []
        for el in localization.get("edit_locations", []):
            lines.append(f"- EDIT   {el.get('file')}  L{el.get('line')}  `{el.get('symbol', '')}`  — {el.get('reason', '')}")
        for nl in localization.get("new_locations", []):
            lines.append(f"- ADD    {nl.get('file')}  after L{nl.get('after_line')} `{nl.get('after_symbol', '')}`  — {nl.get('reason', '')}")
        for tl in localization.get("test_locations", []):
            lines.append(f"- TEST   {tl.get('file')}  follow pattern at L{tl.get('line')} `{tl.get('symbol', '')}`")
        return "\n".join(lines) if lines else "(no specific locations identified)"

    def _write_output(self, result: dict, issue_number: int):
        out_dir = self.output_dir / f"issue-{issue_number}"
        out_dir.mkdir(parents=True, exist_ok=True)

        (out_dir / "result.json").write_text(json.dumps(result, indent=2, default=str))
        (out_dir / "pr_summary.md").write_text(result["pr_summary"])
        (out_dir / "changes.patch").write_text(result["diff"])

        print(f"\n[output] Written to {out_dir}/")
        print(f"\n{'='*60}\nPR SUMMARY\n{'='*60}")
        print(result["pr_summary"])
        print(f"{'='*60}")
        print(f"Tests: {'PASS ✓' if result['tests_pass'] else 'FAIL ✗  (self-heal attempted)'}")
        print(f"Patch: {len(result['diff'])} bytes  candidate #{result['candidate_id']}")


# ------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------

def _extract_json_obj(text: str) -> Optional[dict]:
    import re
    # Try ```json block
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # Try raw JSON
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None
