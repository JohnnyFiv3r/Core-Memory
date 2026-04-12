# Benchmarks

Status: canonical benchmark harness scaffold

This package contains in-repo benchmark tooling for long-conversation memory quality.

## Current harness

- `locomo_like/` — LOCOMO-shaped local harness and fixture pack

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

## Design notes

- This harness uses checked-in fixtures with gold labels for deterministic local replay.
- It does **not** hardcode benchmark answers in retrieval code.
- If external benchmark datasets are used later, they should be loaded through the same schema/loader contracts.
