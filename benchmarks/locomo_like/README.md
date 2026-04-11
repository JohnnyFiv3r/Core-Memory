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
```

## Report output

The runner emits machine-readable JSON containing:

- per-case outcomes
- per-bucket accuracy
- latency summary
- latency breakdown (`write_setup_ms` vs `retrieval_ms`)
- queue observability snapshots before/after query phase
- warnings
- run metadata (commit, mode, timestamp)

Optional plain-text summary is printed to stdout.
