# Causal-Continuity Reproducibility Appendix

Status: generated evidence bundle for source commit `8c2cb7d8`.

This appendix records the exact local commands and generated artifacts for the
current causal-continuity benchmark package. The checked-in report is local,
deterministic-fixture evidence only. It does not make LoCoMo, LongMemEval, or
external-memory leaderboard claims.

## Source

- Source commit used to generate the artifacts: `8c2cb7d8`
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
  --run-real-data-local-proxy \
  --out benchmarks/reports/causal-continuity-local-report.json
```

This command runs T1-T5, includes the runtime ablation matrix, and attaches the
real-data contrast rows with the checked-in local proxy executed. It requires
no network and no vendored external corpus.

## Repeat-Run Check

The repeat-run evidence was generated with:

```bash
python3 -m benchmarks.causal_continuity.reproducibility \
  --repeats 5 \
  --require-pass \
  --out benchmarks/reports/causal-continuity-reproducibility.json
```

Result:

- `stable_headlines`: `true`
- `stable_ordered_topk`: `true`
- `status`: `stable`
- `headline_digest`: `a9af1b4525526f68`
- `ordered_topk_digest`: `131494fbf0af51b6`
- `run_count`: `5`

Interpretation: the deterministic local suite now has stable headline metrics
and stable T5 ordered top-k across five repeated runs. The report is suitable as
local fixture evidence for the T1-T5 and runtime-ablation claims it labels as
local/proxy evidence.

## Current Report Snapshot

From `benchmarks/reports/causal-continuity-local-report.json`:

- Faithfulness: `true`
- T1 Core Memory full: `CSR=1.0`, `root=1.0`, `edge_f1=1.0`
- T1 local baselines:
  - `bm25`: `CSR=0.0`
  - `similarity_only`: `CSR=0.0`
  - `dense_vector`: `CSR=0.0`, `status=proxy_executed`
  - `long_context_no_memory`: `CSR=0.0`, `status=proxy_executed`
  - `external_memory_adapter`: `status=unavailable`,
    `availability=requires_external_memory_adapter`
- T2 calibration: `pass=true`, `rho=0.974679`, `samples=20`
- T3 temporal state selection: `pass=true`
- T4 longitudinal continuity: `pass=true`
- T5 thread fidelity: `pass=true`
- Ablation matrix:
  - `needs_runtime_toggle_rows=0`
  - `observed_expected_drop_rows=7`
  - `observed_no_expected_drop_rows=0`
  - `faithfulness_clean=true`
- Real-data contrast:
  - `dataset_count=3`
  - `external_dataset_count=2`
  - `leaderboard_claim_count=0`
  - `external_adapter_smoke_count=0`
  - `external_eval_smoke_count=0`

## Evidence Manifest

The committed report includes
`evidence_manifest.schema_version=causal_continuity.evidence_manifest.v1`.
It makes claim readiness explicit:

- `local_deterministic.status=ready`
- `proxy_comparator.status=proxy_only`
- `configured_adapter.status=unavailable`
- `real_data_external.status=dataset_required`
- `t5_judge.status=deterministic_default`

Claim gates:

- `local_fixture_claim_ready=true`
- `provider_backed_comparison_ready=false`
- `real_data_leaderboard_ready=false`
- `t5_llm_judge_primary_claim_ready=false`

Interpretation: the checked-in bundle is publishable as deterministic local
fixture evidence, while provider-backed comparisons, official external-corpus
claims, and LLM-judge primary claims remain visibly gated off.

## Claim Certificate

The local claim scope can be checked from the committed report without rerunning
the suite:

```bash
python3 -m benchmarks.causal_continuity.claims \
  --report benchmarks/reports/causal-continuity-local-report.json \
  --require local_fixture \
  --pretty
```

This command passes only for evidence scopes already supported by
`evidence_manifest`. Requests for `provider_backed_comparison`,
`real_data_leaderboard`, or `t5_llm_judge_primary` remain blocked for the
checked-in local report.

## External Evidence Attestation

Future provider-backed, real-data leaderboard, or T5 LLM-primary claims require
both a completed configured run and a reviewer-backed
`causal_continuity.evidence_attestation.v1` payload passed to the suite with
`--evidence-attestation`. The checked-in local report does not include an
external attestation, so its external gates remain closed by design.

## Dependency And Degradation Notes

The local run recorded these warnings:

- `execute_llm_unavailable`
- `external_memory_adapter_not_configured`
- `no_upstream_edges`
- `semantic_backend_query_error:runtimeerror`
- `semantic_backend_query_failed_lexical_fallback`
- `semantic_backend_unavailable_degraded`
- `semantic_index_stale`

Observed environment degradation:

- Qdrant semantic/vector service was not configured; local semantic behavior
  used degraded lexical fallback.
- No external memory adapter or provider-backed T1 command adapter was
  configured.
- Long-context/no-memory ran as a deterministic local proxy, not a public
  provider-backed comparison.
- No LoCoMo or LongMemEval corpus path was supplied for external-corpus smoke or
  evaluation-smoke execution.

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
  --run-real-data-eval-smoke \
  --locomo-corpus path/to/locomo10.json \
  --longmemeval-corpus path/to/longmemeval_s.json
```

Direct LongMemEval loader smoke:

```bash
python3 -m benchmarks.longmemeval \
  --corpus path/to/longmemeval_s.json \
  --limit 1 \
  --eval-smoke \
  --pretty
```

These commands validate adapter readiness and bounded lifecycle evaluation
smoke. They do not create leaderboard claims and they do not vendor the corpora
into this repository.

## Remaining Evidence Limitations

- Public comparison claims against provider-backed long-context/no-memory or
  external-memory systems still require actual configured command-adapter runs
  and documented external-system configuration.
- Real-data rows are local contrast evidence until external corpora are supplied
  and evaluated under their benchmark rules.
- T5 LLM-judge answerability remains supplemental and opt-in; the default
  checked-in claim is the deterministic thread-fidelity metric.
