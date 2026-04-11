# LOCOMO-like benchmark harness

Status: draft implementation scaffold (BH-1/BH-2)

This harness provides an in-repo, deterministic benchmark fixture pack for memory retrieval behavior.

## Layout

- `fixtures/*.jsonl` — benchmark case definitions
- `gold/*.json` — expected labels/outcomes keyed by case id
- `schema.py` — fixture/gold validation and loading
- `runner.py` — benchmark execution entrypoint
- `reporting.py` — report assembly and human summary rendering

## Case buckets

Current fixture pack includes at least one case in each bucket:

- current-state factual recall
- historical/as-of recall
- contradiction/update
- causal/mechanism
- entity/coreference
- preference/identity/policy/commitment/condition

## Invocation

```bash
python -m benchmarks.locomo_like.runner --subset local
python -m benchmarks.locomo_like.runner --subset full
python -m benchmarks.locomo_like.runner --subset local --async-profile drain_before_query
python -m benchmarks.locomo_like.runner --subset local --semantic-mode degraded_allowed --vector-backend local-faiss
python -m benchmarks.locomo_like.runner --subset local --myelination on
python -m benchmarks.locomo_like.runner --subset local --myelination compare
```

## Report output

The runner emits machine-readable JSON containing:

- per-case outcomes
- per-bucket accuracy
- latency summary
- latency breakdown (`write_setup_ms` vs `retrieval_ms`)
- queue observability snapshots before/after query phase
- backend observability (`benchmark_backend_mode`, semantic doctor fields)
- dreamer correlation summary (accepted proposal use-rate in retrieval)
- optional myelination comparison summary (`--myelination compare`)
- warnings
- run metadata (commit, mode, timestamp)

## Benchmark backend clarity

- `--semantic-mode required` + missing semantic backend will reflect `strict_missing_backend` in case metadata.
- `--semantic-mode degraded_allowed` with no built semantic backend reports `degraded_lexical`.
- `--vector-backend local-faiss` reports `local_single_writer` when usable.
- external backends (`qdrant`, `pgvector`) report `external_distributed` when usable.

Optional plain-text summary is printed to stdout.
