# Causal-Continuity Evaluation Suite

Status: PR1 suite harness

This package composes construct-valid causal-continuity benchmark tasks into a
single report. PR1 wires the existing T1 causal-chain reconstruction benchmark
into a strategy matrix so Core Memory can be compared against non-causal
baselines using the same fixtures and scoring logic.

## T1 Strategies

| Strategy | What it does |
|---|---|
| `core_memory_full` | Materializes fixture histories through the public write path, then runs Core Memory recall with causal traversal enabled. |
| `bm25` | Materializes the same histories, then ranks bead text with a deterministic lexical BM25 scorer. It does not inspect causal edges. |
| `similarity_only` | Materializes the same histories, then ranks bead text with a deterministic token and character n-gram similarity proxy. It does not inspect causal edges. |

The headline T1 metric remains **Causal Survival Rate**: in adversarial cases,
the gold root cause must outrank every closest-text distractor.

## Quick Start

Run the full PR1 matrix:

```bash
python -m benchmarks.causal_continuity.runner --subset full
```

Run a fast local baseline-only smoke:

```bash
python -m benchmarks.causal_continuity.runner --subset local --limit 1 --strategies bm25,similarity_only
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
- `tasks.t1_causal_chain_reconstruction.strategy_matrix` — compact per-strategy
  rows for table generation.
- `tasks.t1_causal_chain_reconstruction.strategy_reports` — the full existing
  causal benchmark report for each strategy.
