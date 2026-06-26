# Causal-Continuity Evaluation Suite

Status: causal-continuity suite harness

This package composes construct-valid causal-continuity benchmark tasks into a
single report. It wires the existing T1 causal-chain reconstruction benchmark
into a strategy matrix and adds a T2 calibration-reliability task over the
shipped myelination calibration meter, a T3 temporal state-selection task, and a
T4 longitudinal continuity task over Dreamer lift, self-model drift, and goal
thread persistence, plus a T5 thread-fidelity task over trace-backed storyline
selection.

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

## T3 Temporal State Selection

T3 reframes the LOCOMO-like temporal buckets away from answer-token overlap and
toward state correctness. The checked-in fixture materializes claims and
claim-update rows through canonical write helpers, then scores
`resolve_all_current_state(as_of=...)`.

Metrics:

- Correct state-selection rate.
- As-of accuracy for timestamped historical/current probes.
- Supersession-respect rate: superseded claims or old values are not current.
- Contradiction-surfaced rate: conflicts remain visible instead of being
  silently flattened.

## T4 Longitudinal Continuity

T4 seeds a deterministic multi-session store through Core Memory write and
review paths, then scores the existing longitudinal and self-model quality
meters:

- Continuity lift: `core_with_dreamer_vs_no_memory_lift` from
  `longitudinal_benchmark_v2()`.
- Self-model drift score/status from `compute_self_model_drift()`.
- Goal-thread persistence rate from current Goal Beads.

The fixture requires one accepted/applied structural Dreamer candidate, a
grounded endorsed identity revision, and a reviewed goal thread that stays
active across the run.

## T5 Thread Fidelity

T5 scores whether query-anchored trace expansion returns the right storyline
segment without drifting into a nearby off-thread chain. The checked-in fixture
contains a gold causal storyline and a high-similarity distractor storyline.

The local harness uses a deterministic proxy for the PRD-E agentic loop:

- `trace_request()` supplies semantic seed plus causal expansion.
- Storyline candidates are re-scored against the original query at each step.
- Answerability is scored post-hoc from gold labels for stable CI; no external
  LLM judge is invoked in this slice.

Metrics:

- Thread precision, recall, and F1 for the returned storyline segment.
- Deterministic answerability proxy.
- Query-drift rate for off-thread beads admitted into the segment.

## Ablation Matrix

The optional ablation attachment summarizes the PRD §7 mechanism-ownership rows
from the same suite output. Rows with current telemetry are marked `observed`
when the expected drop appears, `observed_no_expected_drop` when the proxy ran
but did not show the expected effect, and `needs_runtime_toggle` when the row
still needs a dedicated disabled-mode run.

This keeps the report useful before every toggle exists: reviewers can see the
full-system scores, the observed strategy/cohort/baseline deltas, and the
remaining instrumentation gaps in one object.

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

Run only the T3 temporal state-selection task:

```bash
python -m benchmarks.causal_continuity.runner --tasks t3
```

Run only the T4 longitudinal continuity task:

```bash
python -m benchmarks.causal_continuity.runner --tasks t4
```

Run only the T5 thread-fidelity task:

```bash
python -m benchmarks.causal_continuity.runner --tasks t5
```

Emit a suite report:

```bash
python -m benchmarks.causal_continuity.runner --subset full --out benchmarks/reports/causal-continuity.json
```

Emit a suite report with the ablation matrix:

```bash
python -m benchmarks.causal_continuity.runner --subset local --strategies all --include-ablations --out benchmarks/reports/causal-continuity-ablations.json
```

## Report Shape

The top-level report uses `causal_continuity_report.v1` and includes:

- `faithfulness` — the benchmark shortcut flags rolled up by strategy.
- `headlines.t1_causal_chain_reconstruction` — CSR, root-cause accuracy, and
  edge-F1 by strategy.
- `headlines.t2_calibration_reliability` — Spearman rho, ECE, Brier score,
  high-band usefulness, sample count, and pass/fail.
- `headlines.t3_temporal_state_selection` — correct-state, as-of,
  supersession, and contradiction-surfacing rates.
- `headlines.t4_longitudinal_continuity` — continuity lift, self-model drift,
  goal-thread persistence, and applied structural candidate count.
- `headlines.t5_thread_fidelity` — thread precision/recall/F1, answerability,
  query drift, and case count.
- `tasks.t1_causal_chain_reconstruction.strategy_matrix` — compact per-strategy
  rows for table generation.
- `tasks.t2_calibration_reliability.metrics` — scored calibration metrics.
- `tasks.t3_temporal_state_selection.metrics` — scored temporal state-selection
  metrics.
- `tasks.t4_longitudinal_continuity.metrics` — scored longitudinal continuity,
  self-model drift, and goal-thread persistence metrics.
- `tasks.t5_thread_fidelity.metrics` — scored storyline-thread precision,
  recall, answerability, and drift metrics.
- `ablation_matrix` — optional PRD §7 mechanism rows when
  `--include-ablations` is passed.
- `tasks.t1_causal_chain_reconstruction.strategy_reports` — the full existing
  causal benchmark report for each strategy.
