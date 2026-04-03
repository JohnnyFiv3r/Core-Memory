# Runtime Contract Clarity (T6)

Status: Canonical runtime semantics note
Related: `memory.execute`, `memory.search`, `memory.trace`

## Purpose
Define contributor-facing semantics for confidence, next_action, grounding, and source provenance fields.

## Confidence semantics
- `high`: warnings are empty/benign and anchor/grounding conditions are met.
- `medium`: answerable but calibration guardrails prevented high confidence.
- `low`: insufficiently grounded or ambiguous retrieval.

## next_action semantics
- `answer`: safe to answer directly from retrieved context.
- `broaden`: broaden retrieval scope deterministically before final answer (non-causal path).
- `ask_clarifying`: user clarification required due to ambiguity or causal ungrounded state.

## Grounding semantics
- `grounding.required`: true for causal/structural requests.
- `grounding.achieved`: true when structural evidence/chains exist.
- `grounding.reason`: deterministic status reason (`grounded`, `not_required`, `no_structural_edges_found`, `non_temporal_structural_missing`).

## Surface provenance fields (additive)
- `source_surface`: primary provenance surface for top results.
- `source_scope`: `immediate|durable|historical` scope label.
- `source_priority_applied`: explicit preference ordering used for interpretation.

## Determinism policy
- results are normalized to stable order before confidence/next_action evaluation.
- chain ordering is normalized before confidence evaluation.
- warning lists are deduplicated/sorted before calibration diagnostics.
