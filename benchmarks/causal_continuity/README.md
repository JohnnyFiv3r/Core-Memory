# Causal-Continuity Evaluation Suite

Status: causal-continuity suite harness

This package composes construct-valid causal-continuity benchmark tasks into a
single report. It wires the existing T1 causal-chain reconstruction benchmark
into a strategy matrix and adds a T2 calibration-reliability task over the
shipped myelination calibration meter, a T3 temporal state-selection task, and a
T4 longitudinal continuity task over Dreamer lift, self-model drift, and goal
thread persistence, plus a T5 thread-fidelity task over trace-backed storyline
selection.

The remaining paper-evidence closeout sequence is tracked in
`docs/eval/causal-continuity-closeout-plan.md`.

## T1 Strategies

| Strategy | What it does |
|---|---|
| `core_memory_full` | Materializes fixture histories through the public write path, then runs Core Memory recall with causal traversal enabled. |
| `bm25` | Materializes the same histories, then ranks bead text with a deterministic lexical BM25 scorer. It does not inspect causal edges. |
| `similarity_only` | Materializes the same histories, then ranks bead text with a deterministic token and character n-gram similarity proxy. It does not inspect causal edges. |
| `dense_vector` | Emits the dense-vector comparator row using the deterministic local similarity proxy until an external vector baseline is configured. It is labeled `proxy_executed` and does not inspect causal edges. |
| `long_context_no_memory` | Executes a deterministic local context-window proxy with no memory state or causal traversal. It is labeled `proxy_executed` and does not make provider-backed comparison claims. `--long-context-adapter command --long-context-command ...` runs a configured command adapter instead. |
| `external_memory_adapter` | Declares the external-memory comparator row as `unavailable` unless an adapter is configured. `--external-memory-adapter fake` exercises the offline contract path in tests; `--external-memory-adapter command --external-memory-command ...` runs a configured command adapter. |

The headline T1 metric remains **Causal Survival Rate**: in adversarial cases,
the gold root cause must outrank every closest-text distractor.

## T1 Command Adapter Protocol

Configured T1 comparator commands read one JSON object from stdin and write one
JSON object to stdout. The request is graph-blind and answer-key-free: it
includes the query, fixture document keys, document text, and lightweight source
metadata, but not causal edges, bead IDs, distractor labels, or gold answers.

Request schema:

```json
{
  "schema_version": "causal_continuity.t1_adapter_request.v1",
  "task_id": "t1_causal_chain_reconstruction",
  "strategy": "external_memory_adapter",
  "case_id": "case-id",
  "query": "why did the issue happen",
  "intent": "causal",
  "k": 8,
  "documents": [
    {
      "key": "stable-fixture-key",
      "title": "Document title",
      "text": "Document text",
      "metadata": {"type": "context"}
    }
  ],
  "constraints": {
    "includes_causal_edges": false,
    "includes_gold_labels": false,
    "uses_causal_traversal": false,
    "leaderboard_claim": false
  }
}
```

Response schema:

```json
{
  "schema_version": "causal_continuity.t1_adapter_response.v1",
  "status": "completed",
  "adapter_name": "my_comparator",
  "ranked_keys": [
    {"key": "stable-fixture-key", "score": 0.91, "reason": "optional"}
  ],
  "warnings": []
}
```

Command adapter rows still carry `leaderboard_claim: false`; they prove the
execution path and produce local comparison rows, but public external-system
claims require an explicitly documented configured run.

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
- Answerability is scored post-hoc from gold labels for stable CI.
- Optional supplemental judges can be selected with `--t5-judge fake_llm` for
  offline contract tests or `--t5-judge llm` with
  `CORE_MEMORY_T5_LLM_JUDGE_COMMAND` configured.

Metrics:

- Thread precision, recall, and F1 for the returned storyline segment.
- Deterministic answerability proxy.
- Query-drift rate for off-thread beads admitted into the segment.

## Ablation Matrix

The optional ablation attachment has two modes. `--include-ablations` summarizes
the PRD §7 mechanism-ownership rows from the same suite output. Rows with
current telemetry are marked `observed` when the expected drop appears,
`observed_no_expected_drop` when the proxy ran but did not show the expected
effect, and `needs_runtime_toggle` when the row still needs a dedicated
disabled-mode run.

This keeps the report useful before every toggle exists: reviewers can see the
full-system scores, the observed strategy/cohort/baseline deltas, and the
remaining instrumentation gaps in one object.

`--run-ablation-toggles` is the heavier paper-evidence mode. It re-runs the
small deterministic fixtures with supported mechanisms disabled:

- T2 without manifest bonus for myelination backpressure.
- T2 without validated outcome feedback.
- T3 without claim-update application for supersession/temporal filtering.
- Existing T1 similarity, T4 Dreamer-off, and the T5 traversal-disabled run are
  folded into the same runtime matrix.

Rows still report `observed_no_expected_drop` when a disabled run executes but
the current fixture does not show the expected drop.

## Real-Data Contrast

The optional real-data contrast attachment records external-benchmark readiness
without turning local fixtures into public leaderboard claims. It reports:

- the checked-in LOCOMO-like local proxy and its opt-in smoke command,
- the existing external LoCoMo adapter surface, marked runnable only when a
  user-supplied corpus path is present, and
- the LongMemEval adapter surface, marked runnable only when a user-supplied
  corpus path is present.

All rows carry `leaderboard_claim: false`. The local proxy can be run inside the
attachment with `--run-real-data-local-proxy`, but that result remains a local
contrast condition. Supplied external corpora can be load-smoked with
`--run-real-data-adapter-smoke` and bounded lifecycle evaluation-smoked with
`--run-real-data-eval-smoke`; those rows validate adapter readiness/evaluation
plumbing without making leaderboard claims.

## Quick Start

Run the full suite:

```bash
python -m benchmarks.causal_continuity.runner --subset full
```

Run a fast local baseline-only smoke with T1 selected:

```bash
python -m benchmarks.causal_continuity.runner --tasks t1 --subset local --limit 1 --strategies bm25,similarity_only
```

Run T1 with all declared comparator rows:

```bash
python -m benchmarks.causal_continuity.runner --tasks t1 --subset local --limit 1 --strategies all
```

Run T1 with a configured external-memory command adapter:

```bash
python -m benchmarks.causal_continuity.runner \
  --tasks t1 \
  --subset local \
  --limit 1 \
  --strategies external_memory_adapter \
  --external-memory-adapter command \
  --external-memory-command "python path/to/adapter.py"
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

Run supported disabled-mode ablations:

```bash
python -m benchmarks.causal_continuity.runner --subset local --strategies all --run-ablation-toggles --out benchmarks/reports/causal-continuity-runtime-ablations.json
```

Run the repeatability check used by the reproducibility appendix:

```bash
python -m benchmarks.causal_continuity.reproducibility --repeats 5 --require-pass --out benchmarks/reports/causal-continuity-reproducibility.json
```

Emit a suite report with the real-data contrast readiness attachment:

```bash
python -m benchmarks.causal_continuity.runner --subset local --strategies bm25 --limit 1 --include-real-data-contrast --out benchmarks/reports/causal-continuity-real-data.json
```

Load-smoke supplied external corpora inside that attachment:

```bash
python -m benchmarks.causal_continuity.runner \
  --tasks t1 \
  --subset local \
  --strategies bm25 \
  --limit 1 \
  --include-real-data-contrast \
  --run-real-data-adapter-smoke \
  --run-real-data-eval-smoke \
  --locomo-corpus path/to/locomo10.json \
  --longmemeval-corpus path/to/longmemeval_s.json
```

Run the checked-in LOCOMO-like local proxy inside that attachment:

```bash
python -m benchmarks.causal_continuity.runner --tasks t1 --subset local --strategies bm25 --limit 1 --include-real-data-contrast --run-real-data-local-proxy
```

## Report Shape

The top-level report uses `causal_continuity_report.v1` and includes:

- `faithfulness` — the benchmark shortcut flags rolled up by strategy.
- `headlines.t1_causal_chain_reconstruction` — CSR, root-cause accuracy, and
  edge-F1 by strategy, plus strategy status and availability maps.
- `headlines.t2_calibration_reliability` — Spearman rho, ECE, Brier score,
  high-band usefulness, sample count, and pass/fail.
- `headlines.t3_temporal_state_selection` — correct-state, as-of,
  supersession, and contradiction-surfacing rates.
- `headlines.t4_longitudinal_continuity` — continuity lift, self-model drift,
  goal-thread persistence, and applied structural candidate count.
- `headlines.t5_thread_fidelity` — thread precision/recall/F1, answerability,
  query drift, and case count.
- `tasks.t1_causal_chain_reconstruction.strategy_matrix` — compact per-strategy
  rows for table generation, including `status`, `availability`,
  `unavailable_reason`, `failure_reason`, `execution_mode`, `adapter_status`,
  `adapter_name`, `uses_causal_traversal`, and `leaderboard_claim`.
- `tasks.t2_calibration_reliability.metrics` — scored calibration metrics.
- `tasks.t3_temporal_state_selection.metrics` — scored temporal state-selection
  metrics.
- `tasks.t4_longitudinal_continuity.metrics` — scored longitudinal continuity,
  self-model drift, and goal-thread persistence metrics.
- `tasks.t5_thread_fidelity.metrics` — scored storyline-thread precision,
  recall, answerability, judge answerability, and drift metrics.
- `ablation_matrix` — optional PRD §7 mechanism rows when
  `--include-ablations` is passed.
- `real_data_contrast` — optional `causal_continuity.real_data_contrast.v1`
  readiness object when `--include-real-data-contrast` is passed.
- `evidence_manifest` — `causal_continuity.evidence_manifest.v1`, a
  machine-readable claim gate that separates local deterministic evidence,
  proxy comparator rows, configured adapter execution, external-corpus evidence,
  and supplemental T5 judge evidence.
- `tasks.t1_causal_chain_reconstruction.strategy_reports` — the full existing
  causal benchmark report for each strategy.

## Evidence Manifest

The evidence manifest is the safest place for publishing or dashboard code to
decide what the report can honestly claim. It exposes:

- `local_deterministic`: checked-in fixture evidence plus runtime ablations.
- `proxy_comparator`: local proxy baselines such as dense-vector proxy and
  long-context/no-memory proxy.
- `configured_adapter`: configured T1 command/fake adapter execution and any
  unavailable adapter rows.
- `real_data_external`: external corpus readiness, smoke, and leaderboard-claim
  status.
- `t5_judge`: deterministic default or supplemental LLM-judge status.

The default local report should have `local_fixture_claim_ready: true` and keep
provider, real-data leaderboard, and LLM-judge primary claim gates closed.

## Claim Certificate

Use the claim certificate command when CI, release notes, or a paper appendix
need a deterministic pass/fail answer for a report's claim scope:

```bash
python -m benchmarks.causal_continuity.claims \
  --report benchmarks/reports/causal-continuity-local-report.json \
  --require local_fixture \
  --pretty
```

The command reads an existing report only. It does not rerun benchmarks and it
does not create new evidence. The default `local_fixture` scope passes only when
the report's manifest has `local_fixture_claim_ready=true`.

External scopes are intentionally stricter:

```bash
python -m benchmarks.causal_continuity.claims \
  --report benchmarks/reports/causal-continuity-local-report.json \
  --require provider_backed_comparison
```

That command exits nonzero until a configured provider-backed adapter run and
the corresponding public-comparison gate are present. Available scopes are
`local_fixture`, `provider_backed_comparison`, `real_data_leaderboard`, and
`t5_llm_judge_primary`; `--require all` checks every scope.
