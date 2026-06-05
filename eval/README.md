# Evaluation: Localization Recall

A small, reproducible harness that measures the question evaluators care about most —
**"does the agent find the right files to edit?"** — against *real merged PRs*.

It evaluates the **retrieval layer** (BM25 over the symbol index), which is deterministic
and needs **no API key**. The logic: if retrieval surfaces the files the real PR changed,
the LLM has what it needs; if it doesn't, the LLM is forced to hallucinate. So retrieval
recall is a leading indicator of end-to-end correctness.

## Run it

```bash
pip install -r requirements.txt
python -m eval.localization_eval                # top_k=25 (matches the pipeline)
python -m eval.localization_eval --top-k 15     # sweep k
```

Fully offline — uses the shipped index in `indexes/spf13_cobra/` and the issue text
embedded in `dataset.json`.

## What it reports

For each case, whether each Go file the real PR changed appears in the retrieved set,
and at what **rank**. Source-file recall = "did we surface the file that *must* be edited?"

```
 issue  held-out  src-recall  files (changed → retrieval rank)
#  2060       YES       100%   completions.go→#3, completions_test.go→#4
#  2257                 100%   completions.go→#4, completions_test.go→#3
...
Source-file recall: 5/5 = 100%
```

## Current results (spf13/cobra, top_k=25)

- **Source-file recall: 5/5 (100%)** — including the **held-out** case #2060, whose fixing
  PR (#2061) is **not** in the index, so there is no retrieval leakage. The exact file to
  edit is consistently surfaced in the top ~7 symbols.
- **Docs-only issues** (#1658 `SECURITY.md`, #2298 `user_guide.md`) are correctly *not*
  matched by the Go-only index — but the harness flags that the pipeline lacks a
  **"non-code issue" guard**, so it would still march into the patch loop and hallucinate
  a Go change. That's a known gap, surfaced by this eval.

## The dataset (`dataset.json`)

7 cobra cases (issue text + the real PR's changed files), labelled `code`/`docs` and
`held_out`. Two carry a `base_commit` (the pre-fix state) for full end-to-end runs via
`solve.py --base-commit <sha>`.

> **Leakage note.** The 6 non-held-out cases come from PRs that are in the shipped index.
> Symbol retrieval is independent of the PR-example index, so retrieval recall is still a
> fair measurement — but for *end-to-end* fairness use the held-out case (#2060) or add new
> cases whose fixing PR post-dates the index.

## Limitations / next steps

- Measures **recall**, not **precision** — live runs show the LLM localize step *over*-includes
  files (casts too wide a net). A precision metric (extra files / total) is the natural addition.
- Retrieval-only. An `--llm` mode that also scores the structured-localization step
  (file→function→line) would measure the full localization phase (needs an API key).
- Single repo. Add `indexes/{repo}` + dataset cases to extend to gin / validator / golangci-lint.
