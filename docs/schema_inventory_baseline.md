# Schema Inventory Baseline (Phase T1)

Status: Baseline inventory
Purpose: capture currently observed schema vocabulary prior to normalization changes.

## Bead type sources

### `core_memory/models.py` (enum values)
Observed canonical enum includes (representative):
- session_start, session_end
- goal, decision, tool_call, evidence, outcome, lesson
- checkpoint, precedent, association
- failed_hypothesis, reversal, misjudgment, overfitted_pattern, abandoned_path
- reflection, design_principle

### `extract-beads.py` (historical accepted inputs)
Previously accepted legacy aliases in addition to canonical values:
- promoted_lesson
- promoted_decision

Also observed support for:
- context

## Relationship type sources

### `core_memory/models.py` relationship enum
Observed canonical values include:
- caused_by, led_to, blocked_by, unblocks
- supersedes, superseded_by
- associated_with, contradicts, reinforces, mirrors
- applies_pattern_of, violates_pattern_of
- constraint_transformed_into, solves_same_mechanism
- similar_pattern, transferable_lesson
- generalizes, specializes, structural_symmetry, reveals_bias

### Additional relation semantics used elsewhere
Observed in tests/data/mapping/graph paths:
- supports
- derived_from
- resolves
- follows
- related
- shared_tag

## Operational status/state sources

Observed primary statuses in store/runtime paths:
- open
- candidate
- promoted
- archived
- compacted
- superseded

## Baseline notes

- Type/state overlap existed in historical extraction aliases (`promoted_*`).
- Relationship vocabulary includes both canonical structural types and helper/derived semantics.
- Phase T1 normalization should preserve backward compatibility while making canonical categories explicit.
