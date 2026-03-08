# Canonical Schema Spec (Phase T1)

Status: Canonical (Phase T1)
Purpose: formalize canonical schema layers and normalization rules.

## Layer split

Core Memory schema is split into:
1. **Bead types** — what memory unit a bead is
2. **Edge relationship types** — how beads relate
3. **Operational states/statuses** — how system currently treats a bead

These categories must not be conflated.

## Canonical bead types

Canonical set is defined in `core_memory/schema.py` (`CANONICAL_BEAD_TYPES`).

Key included values:
- session_start, session_end
- goal, decision, tool_call, evidence, outcome, lesson
- checkpoint, precedent, association, context, correction
- failed_hypothesis, reversal, misjudgment, overfitted_pattern, abandoned_path
- reflection, design_principle

## Legacy bead aliases

Accepted as legacy inputs, normalized to canonical values:
- promoted_lesson -> lesson
- promoted_decision -> decision

Rule:
- state/promotion must be represented as operational state, not bead type.

## Canonical relationship types

Canonical/structural relation set is defined in `core_memory/schema.py` (`CANONICAL_RELATION_TYPES`).

Includes model-native values plus observed canonicalized extensions:
- caused_by, led_to, blocked_by, unblocks
- supersedes, superseded_by
- associated_with, contradicts, reinforces, mirrors
- applies_pattern_of, violates_pattern_of
- constraint_transformed_into, solves_same_mechanism
- similar_pattern, transferable_lesson, generalizes, specializes
- structural_symmetry, reveals_bias
- supports, derived_from, resolves, follows

## Derived/helper relation tags

Defined in `core_memory/schema.py` (`DERIVED_RELATION_TYPES`):
- related
- shared_tag

Rule:
- derived/helper tags are not equivalent to canonical structural relations.

## Canonical operational statuses

Defined in `core_memory/schema.py` (`CANONICAL_BEAD_STATUSES`):
- open
- candidate
- promoted
- archived
- compacted
- superseded

## Normalization entrypoints

Defined in `core_memory/schema.py`:
- `normalize_bead_type(...)`
- `is_allowed_bead_type(...)`
- `normalize_relation_type(...)`
- `relation_kind(...)`

## Compatibility policy

- Legacy inputs remain accepted where currently required for compatibility.
- Canonical internal values should be emitted by current write paths.
- No storage migration is required in Phase T1.
