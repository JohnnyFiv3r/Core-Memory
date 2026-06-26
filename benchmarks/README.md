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
  longitudinal continuity, plus T5 thread fidelity and optional ablation rows

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

## Design notes

- This harness uses checked-in fixtures with gold labels for deterministic local replay.
- It does **not** hardcode benchmark answers in retrieval code.
- If external benchmark datasets are used later, they should be loaded through the same schema/loader contracts.
