# Causal-Continuity Evaluation Suite

Status: causal-continuity suite harness

This package composes construct-valid causal-continuity benchmark tasks into a
single report. It wires the existing T1 causal-chain reconstruction benchmark
into a strategy matrix and adds a T2 calibration-reliability task over the
shipped myelination calibration meter.

## T1 Strategies

| Strategy | What it does |
|---|---|
| `core_memory_full` | Materializes fixture histories through the public write path, then runs Core Memory recall with causal traversal enabled. |
| `bm25` | Materializes the same histories, then ranks bead text with a deterministic lexical BM25 scorer. It does not inspect causal edges. |
| `similarity_only` | Materializes the same histories, then ranks bead text with a deterministic token and character n-gram similarity proxy. It does not inspect causal edges. |

The headline T1 metric remains **Causal Survival Rate**: in adversarial cases,
the gold root cause must outrank every closest-text distractor.

## T2 Calibration

T2 seeds a checked-in synthetic calibration slice with known useful and
misleading edges. Each edge carries a judge prior plus a manifest bonus, so the
calibration X-axis is the same effective confidence used by the runtime:

```
effective_confidence = clamp(judge_prior + manifest_bonus, 0, 1)
```

The task records retrieval-feedback outcomes and scores the existing
`compute_calibration_curve()` output with:

- Spearman rho between confidence bands and realized usefulness.
- Expected calibration error.
- Brier score.
- High-band usefulness and auto-mode gate state.

## Quick Start

Run the full suite:

```bash
python -m benchmarks.causal_continuity.runner --subset full
```

Run a fast local baseline-only smoke with T1 selected:

```bash
python -m benchmarks.causal_continuity.runner --tasks t1 --subset local --limit 1 --strategies bm25,similarity_only
```

Run only the T2 calibration task:

```bash
python -m benchmarks.causal_continuity.runner --tasks t2
```

Emit a suite report:

```bash
python -m benchmarks.causal_continuity.runner --subset full --out benchmarks/reports/causal-continuity.json
```

## Report Shape

The top-level report uses `causal_continuity_report.v1` and includes:

- `faithfulness` — the benchmark shortcut flags rolled up by strategy.
- `headlines.t1_causal_chain_reconstruction` — CSR, root-cause accuracy, and
  edge-F1 by strategy.
- `headlines.t2_calibration_reliability` — Spearman rho, ECE, Brier score,
  high-band usefulness, sample count, and pass/fail.
- `tasks.t1_causal_chain_reconstruction.strategy_matrix` — compact per-strategy
  rows for table generation.
- `tasks.t2_calibration_reliability.metrics` — scored calibration metrics.
- `tasks.t1_causal_chain_reconstruction.strategy_reports` — the full existing
  causal benchmark report for each strategy.
