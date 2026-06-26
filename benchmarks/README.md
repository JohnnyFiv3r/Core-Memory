# Benchmarks

Status: canonical benchmark harness scaffold

This package contains in-repo benchmark tooling for long-conversation memory quality.

## Current harness

- `locomo_like/` — LOCOMO-shaped local harness and fixture pack (semantic QA)
- `causal/` — causal-chain reconstruction harness with adversarial distractors
  (edge precision/recall, grounding, root-cause accuracy, distractor survival)
- `causal_continuity/` — suite-level causal-continuity report harness; compares
  T1 Core Memory causal traversal against lexical/similarity baselines and
  scores T2 calibration reliability, T3 temporal state selection, and T4
  longitudinal continuity, plus T5 thread fidelity, optional ablation rows, and
  an optional real-data contrast readiness attachment
- `longmemeval/` — LongMemEval adapter-load smoke harness for user-supplied
  JSON/JSONL corpora; validates the shared `BenchmarkAdapter` contract without
  vendoring the dataset or making leaderboard claims

## Quick start

Run the checked-in local subset:

```bash
python -m benchmarks.locomo_like.runner --subset local
```

Run all checked-in fixtures:

```bash
python -m benchmarks.locomo_like.runner --subset full
```

Emit report to file:

```bash
python -m benchmarks.locomo_like.runner --subset local --out benchmarks/reports/local.json
```

Run the causal-chain reconstruction benchmark:

```bash
python -m benchmarks.causal.runner --subset full
```

Run the causal-continuity PR1 strategy matrix:

```bash
python -m benchmarks.causal_continuity.runner --subset full
```

Attach the real-data contrast readiness report:

```bash
python -m benchmarks.causal_continuity.runner --subset local --limit 1 --strategies bm25 --include-real-data-contrast
```

Smoke a user-supplied LongMemEval corpus:

```bash
python -m benchmarks.longmemeval --corpus path/to/longmemeval_s.json --limit 1 --pretty
```

## Design notes

- This harness uses checked-in fixtures with gold labels for deterministic local replay.
- It does **not** hardcode benchmark answers in retrieval code.
- External benchmark datasets should be loaded through `benchmarks.contracts.BenchmarkAdapter`.
- Local proxy fixtures are contrast evidence, not public leaderboard claims.
