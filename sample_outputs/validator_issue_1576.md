# Sample run: go-playground/validator #1576, reproduction-test oracle

A **real, captured** `implement` run (Azure `gpt-5.4-mini`) on a held-out validator bug,
run at the **pre-fix commit**. It shows the reproduction-test oracle end to end: generate a
failing test → fix → verify it passes → pick the candidate that doesn't break the suite.

**Issue:** [\[Bug\]: cron validator accepts arbitrary strings containing a cron-like substring](https://github.com/go-playground/validator/issues/1576)

**Command:**
```bash
python solve.py implement --issue 1576 --repo go-playground/validator \
  --provider azure --base-commit <pre-fix sha>
```

---

## Phase: Reproduce (fail→pass oracle)

```
[reproduce] Generating a fail→pass reproduction test...
  ✓ Repro test TestCronValidationRejectsEmbeddedCronSubstring FAILS on unfixed code
    (valid fail→pass oracle, attempt 1).
```

The agent wrote a test asserting the *correct* behaviour and we verified it actually
**fails** on the current (buggy) code before trusting it. Valid cron strings pass, strings
with junk around a cron substring are rejected:

```go
{"@daily", "cron", true},
{"* * * * *", "cron", true},
{"random text @daily more text", "cron", false},
{"prefix @every 1h suffix", "cron", false},
{"not at all valid; trailing junk: * * * * *", "cron", false},
```

## Phase: Multi-Patch (3 candidates) + select

```
[phase 4/5] Generating 3 patch candidates in parallel...
  Candidate 1: DONE  tests=FAIL  edit=src  repro=PASS
  Candidate 2: DONE  tests=FAIL  edit=src  repro=PASS
  Candidate 3: DONE  tests=PASS  edit=src  repro=PASS
  Best candidate: #3 (repro PASS, tests pass, source edit, diff 2271)
```

All three candidates fix the reproduction test, but #1 and #2 **break the existing
suite**. The ranking `(repro_pass, tests_pass, source_edit, diff)` correctly selects **#3**,
which passes the reproduction test *and* the full suite.

## Phase: Validate

```
  Reproduction test TestCronValidationRejectsEmbeddedCronSubstring: PASS, issue fixed ✓
  Build: PASS
  Tests: PASS
  Vet:   PASS
```

---

## The fix (`regexes.go`)

```diff
-	cronRegexString = `(@(annually|yearly|monthly|weekly|daily|hourly|reboot))|(@every (\d+(ns|us|µs|ms|s|m|h))+)|((((\d+,)+\d+|((\*|\d+)(\/|-)\d+)|\d+|\*) ?){5,7})`
+	cronRegexString = `^((@(annually|yearly|monthly|weekly|daily|hourly|reboot))|(@every (\d+(ns|us|µs|ms|s|m|h))+)|((((\d+,)+\d+|((\*|\d+)(\/|-)\d+)|\d+|[\?\*]) ?){5,7}))$`
```

Anchors the pattern with `^…$` so a value only validates when the **entire** string is a
cron expression (fixing the substring false-positive), matching the maintainer PR #1577's
intent ("anchor regex and accept full cron syntax").

## PR summary (generated)

**Title:** Fix cron validator to match the entire input

> **What:** The `cron` validator accepted any string containing a cron-like substring
> because the regex was not anchored. This anchors the pattern to the full input and updates
> the tests.
> **Why:** Validation should pass only when the entire value is a valid cron expression.
> **Testing:** Added valid cron cases and invalid strings with embedded cron expressions;
> all tests pass.
>
> Closes #1576

---

**result.json (excerpt):**
```json
{
  "repro_valid": true,
  "repro_test": "TestCronValidationRejectsEmbeddedCronSubstring",
  "repro_pass": true,
  "tests_pass": true,
  "build_ok": true,
  "vet_ok": true,
  "candidate_id": 3
}
```

This is the difference between *"matched the right files"* and *"actually solved it, verified
by a test the system wrote itself."*
