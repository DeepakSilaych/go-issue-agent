"""
LangGraph implementation of the 5-phase issue-fixing pipeline.

Graph shape:

    START
      │
      ▼
    setup ── retrieve ── localize ── plan
                                       │
                          (fan-out via Send, 1 per candidate)
                                       ▼
                               patch_candidate ×N   (each in its own git worktree,
                                       │             driven by create_react_agent)
                                       ▼
                                 select_patch  (rank by tests, apply best diff)
                                       ▼
                                   validate  ◀────────┐
                                       │              │
                            (tests fail & go avail)   │
                                       ▼              │
                                   self_heal ─────────┘
                                       │
                              (tests pass / no go / out of rounds)
                                       ▼
                                    summary ── END

The LLM layer is LangChain chat models; the patch and self-heal loops use the
`create_react_agent` prebuilt. Everything else (retrieval, indexing, repo map, Go
toolchain, worktrees) is the project's own code, reused unchanged.
"""

import json
import operator
import subprocess
from pathlib import Path
from typing import Annotated, Optional, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

# create_agent is the LangChain v1 API; fall back to the LangGraph prebuilt on older installs.
try:
    from langchain.agents import create_agent as create_react_agent
except ImportError:  # pragma: no cover
    from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from .github_client import Issue, clone_or_update_repo, create_fix_branch
from .go_utils import Worktree, find_docker, find_go, get_diff, go_available, go_build, go_test, go_vet
from .indexer import build_symbol_index, build_test_helpers, index_exists, load_index
from .llm_client import make_chat_model
from .repomap import build_repo_map
from .retrieval import (
    format_prs_for_prompt,
    format_symbols_for_prompt,
    retrieve_similar_prs,
    retrieve_symbols,
)
from .tools import make_tools

MAX_PATCH_RECURSION = 60   # create_react_agent step budget per candidate
MAX_HEAL_ROUNDS = 3
NUM_CANDIDATES = 3

# Per-candidate strategy nudges — each tries a slightly different approach.
CANDIDATE_STRATEGIES = [
    "Follow the fix plan closely. Prefer minimal changes — only touch what is necessary.",
    "Focus on correctness and edge cases. Add more test coverage than the minimal plan suggests.",
    "Follow the project's existing patterns as closely as possible. Find the most similar existing function and model your implementation after it.",
]


# ---------------------------------------------------------------------------
# Structured localization schema (replaces hand-rolled JSON extraction)
# ---------------------------------------------------------------------------

class _EditLocation(BaseModel):
    file: str
    line: int = 1
    symbol: str = ""
    reason: str = ""


class _NewLocation(BaseModel):
    file: str
    after_line: int = 1
    after_symbol: str = ""
    reason: str = ""


class _TestLocation(BaseModel):
    file: str
    line: int = 1
    symbol: str = ""
    reason: str = ""


class Localization(BaseModel):
    """Where the fix should go, at function/line granularity."""
    edit_locations: list[_EditLocation] = Field(default_factory=list)
    new_locations: list[_NewLocation] = Field(default_factory=list)
    test_locations: list[_TestLocation] = Field(default_factory=list)
    context_files: list[str] = Field(default_factory=list)
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class PipelineState(TypedDict, total=False):
    # constants / config
    issue: Issue
    clone_dir: str
    output_dir: str
    project_rules: str
    # setup outputs
    repo_path: str
    index: dict
    repo_map: str
    # retrieve outputs
    symbols_text: str
    prs_text: str
    # localize / plan outputs
    localization: dict
    file_contents: str
    fix_plan: str
    edit_locations_text: str
    # per-candidate (carried in Send payloads)
    candidate_id: int
    strategy: str
    # patch outputs (accumulated across parallel candidates)
    candidates: Annotated[list, operator.add]
    best_candidate: dict
    # validate / heal outputs
    final_diff: str
    tests_pass: bool
    build_ok: Optional[bool]
    vet_ok: Optional[bool]
    vet_output: str
    test_output: str
    heal_rounds: int
    # final
    pr_summary: str
    result: dict


# ---------------------------------------------------------------------------
# Prompt / rules loading + small helpers
# ---------------------------------------------------------------------------

def _load_prompt(name: str) -> str:
    return (Path(__file__).parent.parent / "prompts" / f"{name}.md").read_text()


def load_rules(repo: str) -> str:
    rules_file = Path(__file__).parent.parent / "rules" / f"{repo.split('/')[-1]}.md"
    return rules_file.read_text() if rules_file.exists() else "(No project-specific rules.)"


def _text(msg) -> str:
    """Extract plain text from an AIMessage whose content may be a string or block list."""
    c = getattr(msg, "content", msg)
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in c
        )
    return str(c)


def _ensure_index(repo: str, repo_path: str) -> dict:
    if index_exists(repo):
        index = load_index(repo)
        print(f"  Loaded index: {len(index['symbols'])} symbols, {len(index['pr_examples'])} PRs")
        return index
    print("  No pre-built index found — building symbol index on the fly...")
    symbols = build_symbol_index(repo_path)
    test_helpers = build_test_helpers(repo_path)
    print(f"  Built {len(symbols)} symbols, {len(test_helpers)} test helpers")
    return {"symbols": symbols, "pr_examples": [], "test_helpers": test_helpers}


def _load_located_files(repo_path: str, localization: dict) -> str:
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
        full = Path(repo_path) / path
        if not full.exists():
            continue
        try:
            content = full.read_text(encoding="utf-8")
            parts.append(f"### {path}\n```go\n{content}\n```")
        except Exception:
            pass
    return "\n\n".join(parts)


def _format_edit_locations(localization: dict) -> str:
    lines = []
    for el in localization.get("edit_locations", []):
        lines.append(f"- EDIT   {el.get('file')}  L{el.get('line')}  `{el.get('symbol', '')}`  — {el.get('reason', '')}")
    for nl in localization.get("new_locations", []):
        lines.append(f"- ADD    {nl.get('file')}  after L{nl.get('after_line')} `{nl.get('after_symbol', '')}`  — {nl.get('reason', '')}")
    for tl in localization.get("test_locations", []):
        lines.append(f"- TEST   {tl.get('file')}  follow pattern at L{tl.get('line')} `{tl.get('symbol', '')}`")
    return "\n".join(lines) if lines else "(no specific locations identified)"


def _print_localization(loc: dict):
    for el in loc.get("edit_locations", []):
        print(f"  EDIT   {el.get('file')}:{el.get('line')}  {el.get('symbol', '')}")
    for nl in loc.get("new_locations", []):
        print(f"  NEW    {nl.get('file')} after L{nl.get('after_line')}  {nl.get('after_symbol', '')}")
    for tl in loc.get("test_locations", []):
        print(f"  TEST   {tl.get('file')}:{tl.get('line')}  {tl.get('symbol', '')}")
    for cf in loc.get("context_files", [])[:3]:
        print(f"  CTX    {cf}")


def _apply_candidate_diff(repo_path: str, diff: str) -> bool:
    """Apply a unified diff to the working tree. Returns True on success."""
    if not diff or diff == "(no changes)":
        return False
    if "--- " not in diff or "+++ " not in diff:
        print(f"  Warning: diff doesn't look like a unified diff ({len(diff)} bytes) — skipping apply")
        return False
    subprocess.run(["git", "checkout", "--", "."], cwd=repo_path, capture_output=True)
    check = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn"],
        input=diff, text=True, cwd=repo_path, capture_output=True,
    )
    if check.returncode != 0:
        print(f"  Warning: diff pre-check failed: {check.stderr[:200]}")
        print("  Attempting 3-way merge fallback...")
        proc = subprocess.run(
            ["git", "apply", "--3way", "--whitespace=nowarn"],
            input=diff, text=True, cwd=repo_path, capture_output=True,
        )
        if proc.returncode != 0:
            print(f"  Warning: 3-way apply also failed: {proc.stderr[:200]}")
            return False
        return True
    proc = subprocess.run(
        ["git", "apply", "--whitespace=nowarn"],
        input=diff, text=True, cwd=repo_path, capture_output=True,
    )
    if proc.returncode == 0:
        print("  Applied diff successfully.")
        return True
    print(f"  Warning: could not apply diff: {proc.stderr[:200]}")
    return False


def _write_output(result: dict, issue_number: int, output_dir: str):
    out_dir = Path(output_dir) / f"issue-{issue_number}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(json.dumps(result, indent=2, default=str))
    (out_dir / "pr_summary.md").write_text(result["pr_summary"])
    (out_dir / "changes.patch").write_text(result["diff"])
    print(f"\n[output] Written to {out_dir}/")
    print(f"\n{'='*60}\nPR SUMMARY\n{'='*60}")
    print(result["pr_summary"])
    print(f"{'='*60}")
    print(f"Tests: {'PASS ✓' if result['tests_pass'] else 'FAIL ✗'}")
    print(f"Patch: {len(result['diff'])} bytes  candidate #{result['candidate_id']}")


# ---------------------------------------------------------------------------
# Graph construction — nodes capture the LLM via closure
# ---------------------------------------------------------------------------

def build_graph(provider: str, model: str):
    """Construct and compile the LangGraph pipeline for a given provider/model."""
    llm = make_chat_model(provider, model)
    system_prompt = _load_prompt("system")

    # ---- Phase 0: setup -------------------------------------------------
    def setup(state: PipelineState) -> dict:
        issue = state["issue"]
        print(f"\n{'='*60}\nIssue #{issue.number}: {issue.title}")
        print(f"Repo: {issue.repo}  Provider: {provider}  Model: {llm.__class__.__name__}")
        print(f"{'='*60}\n")

        go, docker = find_go(), find_docker()
        if go:
            print(f"  Go found at: {go}")
        elif docker:
            print("  Go not found locally — will use Docker (golang image) for tests")
        else:
            print("  WARNING: go binary not found and Docker unavailable — test execution disabled")

        print("[setup] Cloning/updating repository...")
        repo_path = clone_or_update_repo(issue.repo, state["clone_dir"])
        branch = create_fix_branch(repo_path, issue.number)
        print(f"[setup] Branch: {branch}")

        print("[setup] Loading knowledge index...")
        index = _ensure_index(issue.repo, repo_path)
        repo_map = build_repo_map(repo_path, max_files=60)
        return {"repo_path": repo_path, "index": index, "repo_map": repo_map}

    # ---- Phase 1: retrieve ---------------------------------------------
    def retrieve(state: PipelineState) -> dict:
        issue = state["issue"]
        index = state["index"]
        print("\n[phase 1/5] Retrieving relevant symbols and similar PRs...")
        query = f"{issue.title} {issue.body}"
        symbols = retrieve_symbols(query, index["symbols"], top_k=25)
        prs = retrieve_similar_prs(issue.title, issue.body, index["pr_examples"], top_k=3)
        print(f"  Symbols retrieved: {len(symbols)}")
        print(f"  Similar PRs found: {len(prs)}")
        for pr in prs:
            print(f"    - #{pr.get('pr_number')}: {pr.get('pr_title', '')[:60]}")
        return {
            "symbols_text": format_symbols_for_prompt(symbols),
            "prs_text": format_prs_for_prompt(prs),
        }

    # ---- Phase 2: localize ---------------------------------------------
    def localize(state: PipelineState) -> dict:
        issue = state["issue"]
        print("\n[phase 2/5] Deep hierarchical localization...")
        prompt = _load_prompt("localize_deep").format(
            issue_title=issue.title,
            issue_url=issue.url,
            issue_body=issue.body or "(no body)",
            retrieved_symbols=state["symbols_text"],
            repo_map=state["repo_map"],
        )
        loc: dict
        try:
            structured = llm.with_structured_output(Localization).invoke(
                [HumanMessage(content=prompt)]
            )
            loc = structured.model_dump()
        except Exception as e:
            print(f"  Structured localization failed ({type(e).__name__}); using text fallback.")
            loc = _localize_fallback(issue, state["repo_map"])

        _print_localization(loc)
        return {
            "localization": loc,
            "edit_locations_text": _format_edit_locations(loc),
            "file_contents": _load_located_files(state["repo_path"], loc),
        }

    def _localize_fallback(issue: Issue, repo_map: str) -> dict:
        prompt = _load_prompt("localize").format(
            issue_title=issue.title,
            issue_url=issue.url,
            issue_body=issue.body or "(no body)",
            repo_map=repo_map,
        )
        text = _text(llm.invoke([HumanMessage(content=prompt)]))
        parsed = _extract_json_obj(text) or {}
        return {
            "edit_locations": [{"file": f, "line": 1, "symbol": "", "reason": ""}
                               for f in parsed.get("primary_files", [])],
            "new_locations": [],
            "test_locations": [{"file": f, "line": 1, "symbol": "", "reason": ""}
                               for f in parsed.get("test_files", [])],
            "context_files": parsed.get("secondary_files", []),
            "reasoning": parsed.get("reasoning", text[:500]),
        }

    # ---- Phase 3: plan -------------------------------------------------
    def plan(state: PipelineState) -> dict:
        issue = state["issue"]
        print("\n[phase 3/5] Planning fix with PR context...")
        prompt = _load_prompt("plan").format(
            issue_title=issue.title,
            issue_url=issue.url,
            issue_body=issue.body or "(no body)",
            project_rules=state["project_rules"],
            file_contents=state["file_contents"] + f"\n\n## Similar Past PRs\n{state['prs_text']}",
        )
        text = _text(llm.bind(max_tokens=2048).invoke([HumanMessage(content=prompt)]))
        print(f"\n{text[:800]}...\n" if len(text) > 800 else f"\n{text}\n")
        return {"fix_plan": text}

    # ---- Phase 4: fan-out to candidates --------------------------------
    def route_to_candidates(state: PipelineState):
        print(f"\n[phase 4/5] Generating {NUM_CANDIDATES} patch candidates in parallel...")
        return [
            Send("patch_candidate", {
                "candidate_id": i + 1,
                "strategy": CANDIDATE_STRATEGIES[i],
                "issue": state["issue"],
                "fix_plan": state["fix_plan"],
                "edit_locations_text": state["edit_locations_text"],
                "prs_text": state["prs_text"],
                "project_rules": state["project_rules"],
                "repo_path": state["repo_path"],
            })
            for i in range(NUM_CANDIDATES)
        ]

    def patch_candidate(state: PipelineState) -> dict:
        cid = state["candidate_id"]
        issue = state["issue"]
        print(f"  Starting candidate {cid}...")

        with Worktree(state["repo_path"], f"candidate-{cid}") as wt:
            agent = create_react_agent(llm, make_tools(wt))
            prompt = _load_prompt("patch_candidate").format(
                candidate_id=cid,
                total_candidates=NUM_CANDIDATES,
                issue_title=issue.title,
                issue_body=issue.body or "(no body)",
                project_rules=state["project_rules"],
                fix_plan=state["fix_plan"],
                candidate_strategy=state["strategy"],
                similar_prs=state["prs_text"],
                edit_locations=state["edit_locations_text"],
            )
            status = "incomplete"
            try:
                out = agent.invoke(
                    {"messages": [SystemMessage(content=system_prompt),
                                  HumanMessage(content=prompt)]},
                    config={"recursion_limit": MAX_PATCH_RECURSION},
                )
                status = _text(out["messages"][-1]).strip()[:80] or status
            except Exception as e:
                status = f"error: {type(e).__name__}: {e}"[:80]

            diff = get_diff(wt)
            print(f"    [C{cid}] Finished — diff {len(diff)} bytes — {status}")

            tests_pass, test_output = False, ""
            if go_available() and diff != "(no changes)":
                tests_pass, test_output = go_test(wt)

        return {"candidates": [{
            "candidate_id": cid,
            "diff": diff,
            "tests_pass": tests_pass,
            "test_output": test_output[:2000],
            "status": status,
        }]}

    def select_patch(state: PipelineState) -> dict:
        results = state.get("candidates", [])
        for r in results:
            print(f"  Candidate {r['candidate_id']}: {r['status']}  tests={'PASS' if r['tests_pass'] else 'FAIL'}")

        passing = [r for r in results if r["tests_pass"]]
        if passing:
            best = min(passing, key=lambda r: len(r["diff"]))
            print(f"  Best candidate: #{best['candidate_id']} (tests pass, diff {len(best['diff'])})")
        elif results:
            with_changes = [r for r in results if r["diff"] != "(no changes)"]
            pool = with_changes if with_changes else results
            best = min(pool, key=lambda r: len(r["diff"]))
            print(f"  No passing candidates — using #{best['candidate_id']} (smallest diff, best effort)")
        else:
            print("  All candidates failed — returning empty")
            best = {"candidate_id": 0, "diff": "(no changes)", "tests_pass": False,
                    "status": "failed", "test_output": ""}

        applied = _apply_candidate_diff(state["repo_path"], best["diff"])
        if best["diff"] != "(no changes)" and not applied:
            print("  WARNING: best candidate diff did not apply cleanly.")
        best = {**best, "applied": applied}
        return {"best_candidate": best,
                "tests_pass": best["tests_pass"],
                "test_output": best.get("test_output", "")}

    # ---- Phase 5: validate + self-heal + summary -----------------------
    def validate(state: PipelineState) -> dict:
        repo_path = state["repo_path"]
        final_diff = get_diff(repo_path)
        updates = {"final_diff": final_diff}

        if go_available() and final_diff != "(no changes)":
            build_ok, _ = go_build(repo_path)
            tests_pass, test_output = go_test(repo_path)
            vet_ok, vet_out = go_vet(repo_path)
            print("\n[phase 5/5] Validating...")
            print(f"  Build: {'PASS' if build_ok else 'FAIL'}")
            print(f"  Tests: {'PASS' if tests_pass else 'FAIL'}")
            print(f"  Vet:   {'PASS' if vet_ok else 'FAIL'}")
            updates.update(tests_pass=tests_pass, test_output=test_output,
                           build_ok=build_ok, vet_ok=vet_ok, vet_output=vet_out)
        else:
            updates.update(build_ok=None, vet_ok=None, vet_output="")
        return updates

    def should_heal(state: PipelineState) -> str:
        if not go_available():
            return "summary"
        if state.get("final_diff", "(no changes)") == "(no changes)":
            return "summary"
        if state.get("tests_pass"):
            return "summary"
        if state.get("heal_rounds", 0) >= MAX_HEAL_ROUNDS:
            return "summary"
        return "self_heal"

    def self_heal(state: PipelineState) -> dict:
        rnd = state.get("heal_rounds", 0) + 1
        print(f"  Self-heal round {rnd}/{MAX_HEAL_ROUNDS}...")
        issue = state["issue"]
        agent = create_react_agent(llm, make_tools(state["repo_path"]))
        heal_prompt = (
            "The following tests are failing after your fix. Fix the failures using the tools.\n\n"
            f"Issue: {issue.title}\n\n"
            f"Test output:\n```\n{state.get('test_output', '')[:3000]}\n```\n\n"
            "Use read_file, edit_file, and run_command to fix all failures. "
            "When all tests pass, respond: DONE: <summary>"
        )
        try:
            agent.invoke(
                {"messages": [SystemMessage(content=system_prompt),
                              HumanMessage(content=heal_prompt)]},
                config={"recursion_limit": 40},
            )
        except Exception as e:
            print(f"  Self-heal round errored: {type(e).__name__}: {e}")
        return {"heal_rounds": rnd}

    def summary(state: PipelineState) -> dict:
        issue = state["issue"]
        diff = state.get("final_diff", "(no changes)")
        tests_pass = bool(state.get("tests_pass"))
        test_output = state.get("test_output", "")
        best = state.get("best_candidate", {})

        prompt = _load_prompt("validate").format(
            issue_title=issue.title,
            issue_url=issue.url,
            issue_body=issue.body or "(no body)",
            issue_number=issue.number,
            diff=diff[:8000],
            test_results=f"Tests: {'PASS' if tests_pass else 'FAIL'}\n\n{test_output[:2000]}",
        )
        pr_summary = _text(llm.bind(max_tokens=1024).invoke([HumanMessage(content=prompt)]))

        result = {
            "issue": {"number": issue.number, "title": issue.title, "url": issue.url},
            "diff": diff,
            "tests_pass": tests_pass,
            "test_output": test_output,
            "build_ok": state.get("build_ok"),
            "vet_ok": state.get("vet_ok"),
            "vet_output": state.get("vet_output", ""),
            "diff_applied": best.get("applied", diff != "(no changes)"),
            "pr_summary": pr_summary,
            "candidate_id": best.get("candidate_id", 0),
        }
        _write_output(result, issue.number, state["output_dir"])
        return {"pr_summary": pr_summary, "result": result}

    # ---- wire the graph -------------------------------------------------
    g = StateGraph(PipelineState)
    g.add_node("setup", setup)
    g.add_node("retrieve", retrieve)
    g.add_node("localize", localize)
    g.add_node("plan", plan)
    g.add_node("patch_candidate", patch_candidate)
    g.add_node("select_patch", select_patch)
    g.add_node("validate", validate)
    g.add_node("self_heal", self_heal)
    g.add_node("summary", summary)

    g.add_edge(START, "setup")
    g.add_edge("setup", "retrieve")
    g.add_edge("retrieve", "localize")
    g.add_edge("localize", "plan")
    g.add_conditional_edges("plan", route_to_candidates, ["patch_candidate"])
    g.add_edge("patch_candidate", "select_patch")
    g.add_edge("select_patch", "validate")
    g.add_conditional_edges("validate", should_heal, {"self_heal": "self_heal", "summary": "summary"})
    g.add_edge("self_heal", "validate")
    g.add_edge("summary", END)
    return g.compile()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_issue(issue: Issue, clone_dir: str, output_dir: str, provider: str, model: str) -> dict:
    """Run the full pipeline for one issue and return the result dict."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    graph = build_graph(provider, model)
    final_state = graph.invoke(
        {
            "issue": issue,
            "clone_dir": clone_dir,
            "output_dir": output_dir,
            "project_rules": load_rules(issue.repo),
            "candidates": [],
            "heal_rounds": 0,
        },
        config={"recursion_limit": 100},
    )
    return final_state["result"]


# ---------------------------------------------------------------------------
# JSON fallback util (used only if structured localization fails)
# ---------------------------------------------------------------------------

def _extract_json_obj(text: str) -> Optional[dict]:
    import re
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None
