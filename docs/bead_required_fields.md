# Bead Required Fields (v2)

This document defines the v2 required-fields policy for bead quality.

## Rollout

- Default mode: **warn-first** (non-blocking)
- Strict mode: set `CORE_MEMORY_STRICT_REQUIRED_FIELDS=1`
- Promotion is strict for high-value bead types (`decision`, `lesson`, `outcome`, `precedent`)

## Global baseline (all beads)

Required:
- `type`
- `title`
- `summary` (1-3 bullets, <=220 chars each)
- `session_id`
- `source_turn_ids`
- `status`
- `created_at`

Soft-required:
- `tags`

## Type-specific requirements

### decision
- Creation: at least one of `because`, `evidence_refs`/`tool_output_id(s)`, `detail`
- Promotion: requires `because` **and** (`evidence_refs`/`tool_output_id(s)` or `detail`)

### lesson
- Requires `because`
- Promotion requires `because`

### outcome
- Requires `result` in `resolved|failed|partial|confirmed`
- Requires backward linkage (`linked_bead_id` or `links`) or evidence ref

### evidence
- Requires one of evidence refs / tool output IDs / detail>=60
- Requires `supports_bead_ids`

### goal
- Requires `goal_id`
- Requires `success_criteria`

### precedent
- Requires `condition`
- Requires `action`

### design_principle
- Requires `because`

### failed_hypothesis
- `tested_by` allowed values: `tool|reasoning|observation`

### tool_call
- Requires `tool`/`capability`
- `tool_result_status` allowed values: `success|failure`

## Promoted bead explainability

All promoted beads include:
- `promotion_reason` (explicit or auto-filled)

## Link normalization

Canonical link model:

```json
{
  "links": [
    {"type": "supports", "bead_id": "bead-..."},
    {"type": "derived_from", "bead_id": "bead-..."}
  ]
}
```

Legacy dict-style links are normalized to canonical list form at write time.
