# PRD: Eval and Benchmark Layer

**Status:** Spec only — baselines exist, no pipeline  
**Effort:** ~3 days  
**Depends on:** Nothing — can be built in parallel with any other item  
**Baseline reference:** `docs/benchmarks/locomo/baselines.md`

---

## Problem

Recall quality improvements ship without measurement. There is no pipeline to run the
LoCoMo benchmark set, no committed baseline to diff against, and no CI gate to catch
regressions. This means:

- #11 (myelination) may re-rank evidence in a way that helps some queries and regresses
  others — undetectable until a user reports it.
- #13 (temporal recall) may filter correctly on the obvious cases and silently drop
  evidence on edge cases — no test covers it.
- #15 (multi-store fan-out) adds two new result sources that could dilute Core Memory's
  top results — no way to measure the tradeoff.
- External credibility claims ("inspectable recall") require a benchmark delta.
  "It's better" without a number is not credible to a technical audience.

---

## User value

- Every capability item (#11, #13, #14, #15) ships with a committed eval result showing
  its delta vs. baseline — evidence-grounded quality claims.
- CI catches recall regressions before they reach main — a PR that drops precision by
  more than 2 percentage points fails automatically.
- The eval runner is a first-class tool: contributors can run it locally against their
  own changes before opening a PR.

---

## Current state

| Component | Status |
|-----------|--------|
| LoCoMo baseline fixtures | Done — `docs/benchmarks/locomo/baselines.md` |
| Eval runner script | **Missing** |
| Baseline JSON capture | **Missing** |
| CI integration | **Missing** |
| Per-feature delta reports | **Missing** |

---

## Success criteria

1. `python -m eval.locomo_runner` ingests the LoCoMo fixtures into a fresh
   `JsonFileBackend` instance, runs all queries, and writes a JSON report to
   `eval/results/locomo-{date}-{git_sha[:8]}.json`.
2. The report contains precision, recall, and F1 per query type (temporal, causal,
   factual, cross-session) and overall.
3. A committed baseline file at `eval/baselines/locomo-baseline.json` exists after
   the first run. Subsequent runs diff against it and print a delta table.
4. CI runs a 20-query smoke subset on every PR that touches `retrieval/`. The smoke
   set fails the build if any score drops more than 2pp vs. baseline.
5. A full eval run (all queries) runs nightly on `main` and commits an updated result
   file if scores improve.
6. The runner works against `JsonFileBackend` with no external dependencies — no
   vector index, no graph backend, no network calls required.

---

## Scope

**In:**
- `eval/locomo_runner.py` — ingests fixtures, runs queries, scores results, writes report
- `eval/fixtures/locomo/` — LoCoMo test cases extracted from `baselines.md` as structured JSON
- `eval/baselines/locomo-baseline.json` — committed baseline (captured after first run)
- `eval/__main__.py` — CLI entry: `python -m eval [--smoke] [--diff] [--out path]`
- `.github/workflows/eval.yml` — CI: smoke on PR, full nightly
- Per-feature delta: each PRD for #11, #13, #14, #15 requires a committed
  `eval/results/<feature>-delta.json` before the PR is considered complete

**Out:**
- Custom benchmark formats — use LoCoMo fixtures as-is
- LLM-as-judge scoring — string-match and overlap-based scoring only in the first cut
- Semantic scoring (embedding similarity between generated answer and gold) — additive later
- Eval against external fan-out stores (Core Memory recall only in this slice)

---

## Fixture format (`eval/fixtures/locomo/`)

Each LoCoMo test case is one JSON file:

```json
{
  "id": "locomo-001",
  "type": "causal",
  "description": "Cross-session causal chain — decision bead causes outcome",
  "ingest": [
    {
      "session_id": "session-a",
      "turns": [
        { "role": "user",      "content": "..." },
        { "role": "assistant", "content": "..." }
      ]
    }
  ],
  "query": "Why did we switch to the new vendor?",
  "gold_bead_ids": ["<bead id expected in evidence>"],
  "gold_answer_tokens": ["vendor", "cost", "delivery"],
  "query_type": "causal"
}
```

**Query types:** `causal`, `temporal`, `factual`, `cross_session`, `contradiction`

Extract these from `docs/benchmarks/locomo/baselines.md`. If the baselines file
contains prose rather than structured cases, the first task is converting it to
this format and committing the structured fixtures.

---

## Scoring (`eval/locomo_runner.py`)

**Evidence precision:** fraction of returned `bead_id`s that appear in `gold_bead_ids`.  
**Evidence recall:** fraction of `gold_bead_ids` that appear in returned `bead_id`s.  
**Answer F1:** token-level F1 between `RecallResult.answer` and `gold_answer_tokens`.

All three metrics are computed per query. The report aggregates mean per query type
and mean overall.

```python
def score_result(result: RecallResult, case: dict) -> dict:
    returned_ids = {e.bead_id for e in result.evidence if e.bead_id}
    gold_ids     = set(case.get("gold_bead_ids") or [])
    precision    = len(returned_ids & gold_ids) / len(returned_ids) if returned_ids else 0.0
    recall       = len(returned_ids & gold_ids) / len(gold_ids)     if gold_ids     else 1.0
    f1_evidence  = _f1(precision, recall)

    returned_tokens = set(_tokenize(result.answer or ""))
    gold_tokens     = set(case.get("gold_answer_tokens") or [])
    answer_f1       = _token_f1(returned_tokens, gold_tokens)

    return {
        "id": case["id"], "type": case["query_type"],
        "precision": precision, "recall": recall,
        "f1_evidence": f1_evidence, "answer_f1": answer_f1,
    }
```

---

## Report format

```json
{
  "run_at": "<iso8601>",
  "git_sha": "<8-char sha>",
  "backend": "JsonFileBackend",
  "total_cases": 42,
  "smoke": false,
  "scores": {
    "overall": { "precision": 0.72, "recall": 0.68, "f1_evidence": 0.70, "answer_f1": 0.61 },
    "by_type": {
      "causal":       { "precision": 0.80, "recall": 0.75, "f1_evidence": 0.77, "answer_f1": 0.69 },
      "temporal":     { "precision": 0.65, "recall": 0.60, "f1_evidence": 0.62, "answer_f1": 0.55 },
      "factual":      { "precision": 0.78, "recall": 0.74, "f1_evidence": 0.76, "answer_f1": 0.67 },
      "cross_session":{ "precision": 0.68, "recall": 0.63, "f1_evidence": 0.65, "answer_f1": 0.58 },
      "contradiction":{ "precision": 0.60, "recall": 0.55, "f1_evidence": 0.57, "answer_f1": 0.50 }
    }
  },
  "per_case": [
    { "id": "locomo-001", "type": "causal", "precision": 1.0, "recall": 0.5, "f1_evidence": 0.67, "answer_f1": 0.8 }
  ]
}
```

---

## Baseline diff

When `--diff` is passed (or in CI), load `eval/baselines/locomo-baseline.json` and
print a delta table:

```
Metric           Baseline    Current    Delta
────────────────────────────────────────────
overall.f1       0.70        0.73       +0.03 ✓
causal.f1        0.77        0.79       +0.02 ✓
temporal.f1      0.62        0.60       -0.02 ✗ (below threshold)
```

CI gate: if any `by_type` or `overall` metric drops more than 0.02 vs. baseline,
exit non-zero. The PR author must either fix the regression or explicitly update the
baseline with a justification comment in the commit.

---

## Smoke set

20 cases selected from the fixture set to cover all 5 query types (4 per type).
Selection criteria: the 4 highest-variance cases per type (i.e. the ones most likely
to catch regressions). Record smoke case IDs in `eval/fixtures/smoke_set.json`.

The smoke set is deterministic — same 20 cases every run, not randomly sampled.

---

## CI workflow (`.github/workflows/eval.yml`)

```yaml
name: eval
on:
  pull_request:
    paths: ["core_memory/retrieval/**", "eval/**"]
  schedule:
    - cron: "0 3 * * *"   # nightly full run on main

jobs:
  smoke:
    if: github.event_name == 'pull_request'
    steps:
      - run: python -m eval --smoke --diff --out eval/results/smoke-pr-${{ github.sha[:8] }}.json
      # fails build if delta < -0.02 on any metric

  full:
    if: github.event_name == 'schedule'
    steps:
      - run: python -m eval --out eval/results/locomo-$(date +%F)-${{ github.sha[:8] }}.json
      - run: |
          # If overall.f1 improved vs baseline, update baseline and commit
          python -m eval.update_baseline eval/results/locomo-*.json
```

---

## Implementation tasks

1. **`eval/fixtures/locomo/`** — Convert `docs/benchmarks/locomo/baselines.md` to
   structured JSON fixtures. One file per test case. Commit the structured fixtures;
   the prose baselines file remains as a human-readable reference.

2. **`eval/locomo_runner.py`** — Main runner. Creates a temp `JsonFileBackend` dir,
   ingests each fixture's `ingest` turns via `emit_turn_finalized()`, calls `recall()`
   for each query, scores with `score_result()`, writes the JSON report.

3. **`eval/__main__.py`** — CLI: `python -m eval [--smoke] [--diff] [--baseline path]
   [--out path]`. Parse args, call runner, print diff table if `--diff`.

4. **`eval/update_baseline.py`** — Script for nightly CI: compare latest result vs
   baseline; if `overall.f1` improved, overwrite `eval/baselines/locomo-baseline.json`
   and print a changelog line.

5. **`eval/baselines/locomo-baseline.json`** — Capture by running the runner on
   current `main` after step 2 is complete. Commit the result as the initial baseline.

6. **`.github/workflows/eval.yml`** — Implement per the spec above.

7. **Per-feature delta requirement** — Add to each of #11, #13, #14, #15's PRDs:
   "This item ships with a committed `eval/results/<item>-delta.json` showing delta
   vs. baseline." Add to their respective test sections.

---

## Dependencies / risks

- **Fixture extraction quality:** If `docs/benchmarks/locomo/baselines.md` contains
  only prose descriptions rather than structured ingest/query/gold triples, the first
  task (fixture extraction) is the hardest part. Audit the file before estimating.
- **Answer scoring is approximate:** Token F1 on `gold_answer_tokens` is a rough
  proxy. It will pass trivially for exact-match answers and fail for paraphrases. This
  is acceptable for regression detection but not for external benchmark reporting.
  Document this limitation clearly in the README.
- **`emit_turn_finalized` is async-adjacent:** If the ingest path spawns background
  jobs, the runner must drain them before calling `recall()`. Verify whether the runner
  needs a `process_flush()` call between ingest and query phases.
- **JsonFileBackend path isolation:** Each test run must use a fresh temp directory.
  Use `tempfile.mkdtemp()` and clean up after the run. Do not write fixture state to
  the repo's own `.beads/` directory.
