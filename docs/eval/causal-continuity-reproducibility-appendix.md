# Causal-Continuity Reproducibility Appendix

Status: generated evidence bundle for source commit `5f2d72aa`.

This appendix records the exact local commands and generated artifacts for the
current causal-continuity benchmark package. The checked-in report is local,
deterministic-fixture evidence only. It does not make LoCoMo, LongMemEval, or
external-memory leaderboard claims.

## Source

- Source commit used to generate the artifacts: `5f2d72aa`
- Python: `3.14.0`
- Platform recorded by the reproducibility report:
  `macOS-26.5.1-arm64-arm-64bit-Mach-O`
- Generated report:
  `benchmarks/reports/causal-continuity-local-report.json`
- Repeat-run report:
  `benchmarks/reports/causal-continuity-reproducibility.json`

## Primary Command

The committed report was generated with:

```bash
python3 -m benchmarks.causal_continuity.runner \
  --subset local \
  --limit 1 \
  --strategies all \
  --run-ablation-toggles \
  --include-real-data-contrast \
  --out benchmarks/reports/causal-continuity-local-report.json
```

This command runs T1-T5, includes the runtime ablation matrix, and attaches the
real-data contrast readiness rows. It requires no network and no vendored
external corpus.

## Repeat-Run Check

The repeat-run evidence was generated with:

```bash
python3 -m benchmarks.causal_continuity.reproducibility \
  --repeats 3 \
  --out benchmarks/reports/causal-continuity-reproducibility.json
```

Result:

- `stable_headlines`: `true`
- `stable_ordered_topk`: `false`
- `status`: `unstable_ordered_topk`
- `headline_digest`: `880ecbb4d46ce4b9`
- `run_count`: `3`

Interpretation: the minimum headline metrics were stable across repeated local
runs, but the T5 trace/storyline ordered top-k changed order among otherwise
correct gold-thread beads. The current report is therefore suitable as local
fixture evidence for the minimum T1/T2/ablation claim, while T5 ordered-top-k
stability remains an evidence limitation for any paper claim that depends on
ordered thread ranking.

## Current Report Snapshot

From `benchmarks/reports/causal-continuity-local-report.json`:

- Faithfulness: `true`
- T1 Core Memory full: `CSR=1.0`, `root=1.0`, `edge_f1=1.0`
- T1 local baselines:
  - `bm25`: `CSR=0.0`
  - `similarity_only`: `CSR=0.0`
  - `dense_vector`: `CSR=0.0`, `status=proxy_executed`
- T2 calibration: `pass=true`, `rho=0.974679`, `samples=20`
- T3 temporal state selection: `pass=true`
- T4 longitudinal continuity: `pass=true`
- T5 thread fidelity: `pass=true`
- Ablation matrix:
  - `needs_runtime_toggle_rows=0`
  - `observed_expected_drop_rows=5`
  - `observed_no_expected_drop_rows=2`
  - `faithfulness_clean=true`
- Real-data contrast:
  - `dataset_count=3`
  - `external_dataset_count=2`
  - `leaderboard_claim_count=0`
  - `external_adapter_smoke_count=0`

## Dependency And Degradation Notes

The local run recorded these warnings:

- `execute_llm_unavailable`
- `external_memory_adapter_not_configured`
- `long_context_no_memory_adapter_not_configured`
- `no_upstream_edges`
- `semantic_backend_query_error:runtimeerror`
- `semantic_backend_query_failed_lexical_fallback`
- `semantic_backend_unavailable_degraded`
- `semantic_index_stale`

Observed environment degradation:

- Qdrant semantic/vector service was not configured; local semantic behavior
  used degraded lexical fallback.
- Kuzu graph backend was not installed; graph backend construction fell back to
  null where optional graph projection was attempted.
- No external memory adapter was configured.
- No long-context/no-memory adapter was configured.
- No LoCoMo or LongMemEval corpus path was supplied for external-corpus smoke
  execution.

These states are intentionally represented as warnings or unavailable contrast
rows rather than silent successes.

## External-Corpus Commands

When corpora are supplied locally, adapter-smoke readiness can be checked with:

```bash
python3 -m benchmarks.causal_continuity.runner \
  --tasks t1 \
  --subset local \
  --strategies bm25 \
  --limit 1 \
  --include-real-data-contrast \
  --run-real-data-adapter-smoke \
  --locomo-corpus path/to/locomo10.json \
  --longmemeval-corpus path/to/longmemeval_s.json
```

Direct LongMemEval loader smoke:

```bash
python3 -m benchmarks.longmemeval \
  --corpus path/to/longmemeval_s.json \
  --limit 1 \
  --pretty
```

These commands validate adapter readiness only. They do not create leaderboard
claims and they do not vendor the corpora into this repository.

## Remaining Evidence Limitations

- Public comparison claims against long-context/no-memory or external-memory
  systems still require actual configured adapter runs.
- Real-data rows are contrast/readiness evidence until external corpora are
  supplied and evaluated under their benchmark rules.
- T5 ordered-top-k ranking is not stable across repeated local runs yet, even
  though headline thread metrics are stable in this fixture.
