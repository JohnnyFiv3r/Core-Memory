# Myelination Experiment Contract

Status: experimental (MYE-1)

Purpose: evaluate telemetry-driven strengthening/weakening as an optional ranking signal.

Design intent: **edge-first learning**. The experiment learns which traversal paths/relations
are useful, then projects those signals onto endpoint beads for current scorer compatibility.

## Flag

- `CORE_MEMORY_MYELINATION_ENABLED=1` enables experimental myelination bonus scoring.
- Default is disabled.

## Signal Source

Derived from retrieval feedback events:

- `.beads/events/retrieval-feedback.jsonl`

Strengthening/weakening is based on observed success/failure participation counts on
retrieved chain edges, not elapsed time. No time-decay-based node punishment is introduced.

## Scoring Integration

When enabled:

- edge bonuses are derived first (`bonus_by_edge_key`)
- canonical search injects projected endpoint `myelination_bonus` into bead scoring context
- reranker includes `myelination_bonus` as an additive feature

When disabled:

- `myelination_bonus` defaults to zero and has no behavioral impact.

## Benchmark Comparison Path

Benchmark runner supports:

- `--myelination off|on|compare`

`compare` mode executes baseline and enabled passes and emits
`myelination_comparison` summary in report output.
