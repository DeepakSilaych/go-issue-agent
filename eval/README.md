# Evaluation

Two harnesses live here. Both compare the agent against real merged PRs.

- `localization_eval.py`: a small, deterministic check of the retrieval layer. No API key.
- `swe_eval.py`: a SWE-bench-style batch that runs the full agent at each issue's pre-fix
  commit and compares the diff to the real PR. Results in [`SWE_RESULTS.md`](SWE_RESULTS.md).

## Localization recall

This measures the question evaluators care about most: does the agent find the right files to
edit? It scores the retrieval layer (BM25 over the symbol index), which is deterministic and
needs no API key. The reasoning is simple. If retrieval surfaces the files the real PR changed,
the model has what it needs. If it does not, the model is left to guess. So retrieval recall is
a leading indicator of end-to-end correctness.

### Run it

```bash
pip install -r requirements.txt
python -m eval.localization_eval                # top_k=25, matches the pipeline
python -m eval.localization_eval --top-k 15     # sweep k
```

It runs fully offline, using the shipped index in `indexes/spf13_cobra/` and the issue text in
`dataset.json`.

### What it reports

For each case, whether each Go file the real PR changed shows up in the retrieved set, and at
what rank. Source-file recall answers "did we surface the file that has to be edited?"

```
 issue  held-out  src-recall  files (changed -> retrieval rank)
#  2060       YES       100%   completions.go->#3, completions_test.go->#4
#  2257                 100%   completions.go->#4, completions_test.go->#3
...
Source-file recall: 5/5 = 100%
```

### Current results (spf13/cobra, top_k=25)

Source-file recall is 5/5 (100%), including the held-out case #2060, whose fixing PR (#2061) is
not in the index, so there is no leakage. The file to edit consistently shows up in the top
seven or so symbols.

The docs-only issues (#1658 `SECURITY.md`, #2298 `user_guide.md`) are correctly not matched by
the Go-only index. The harness flags that the pipeline has no "non-code issue" guard, so it
would still enter the patch loop and try to make a Go change. That is a known gap this eval
surfaced.

### The dataset (`dataset.json`)

Seven cobra cases (issue text plus the real PR's changed files), labelled `code` or `docs` and
`held_out`. Two carry a `base_commit` (the pre-fix state) for end-to-end runs via
`solve.py implement --base-commit <sha>`.

> Leakage note. The six non-held-out cases come from PRs that are in the shipped index. Symbol
> retrieval is independent of the PR-example index, so retrieval recall is still a fair
> measurement. For end-to-end fairness, use the held-out case (#2060) or add cases whose fixing
> PR post-dates the index.

### Limitations and next steps

- It measures recall, not precision. Live runs show the localize step pulling in extra files.
  A precision metric (extra files over total) is the natural addition.
- Retrieval only. An `--llm` mode that also scores the structured-localization step would cover
  the full localization phase, but it needs an API key.
- One repo. Add `indexes/{repo}` and dataset cases to extend to gin, validator, or
  golangci-lint.
