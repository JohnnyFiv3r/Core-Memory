# Retrieval KPI Targets (Slice 1)

Deterministic baseline targets for canonical retrieval rollout.

- Recall@5 >= 0.60
- MRR >= 0.50
- Median latency (single query local) <= 0.50s
- Determinism: identical ordered top-5 across 5 repeated runs

These are initial guardrails and should be tightened after first stable pass.

KPI fixtures in `eval/kpi_set.json` should remain human-reviewed gold cases; avoid auto-derived expected IDs.
