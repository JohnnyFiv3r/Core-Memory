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

Canonical set is defined in `core_memory/schema/normalization.py` (`CANONICAL_BEAD_TYPES`).

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

Canonical/structural relation set is defined in `core_memory/schema/normalization.py` (`CANONICAL_RELATION_TYPES`).

Includes model-native values plus observed canonicalized extensions:
- caused_by, led_to, blocked_by, unblocks, blocks_unblocks
- supersedes, superseded_by, refines
- associated_with, contradicts, invalidates, diagnoses, reinforces, mirrors
- applies_pattern_of, violates_pattern_of
- constraint_transformed_into, solves_same_mechanism
- similar_pattern, transferable_lesson, generalizes, specializes
- structural_symmetry, reveals_bias
- supports, derived_from, part_of, resolves, follows, precedes, enables

## Relation families and direction rules

Shared helper families are defined in `core_memory/schema/normalization.py` so
Myelination, Assembly Depth, retrieval reporting, and agent guidance do not each
carry private relation sets.

Families are helper classifications only. They do not migrate stored rows or
change graph traversal semantics.

- causal: caused_by, led_to, resolves, diagnoses
- evidence: supports, derived_from, caused_by, led_to, resolves
- influence: blocked_by, unblocks, blocks_unblocks, enables
- conflict: contradicts, invalidates, violates_pattern_of
- temporal: follows, precedes
- revision: supersedes, superseded_by, refines

Direction rules for new authored associations:
- `caused_by`: the source bead is explained by, or exists because of, the target
  cause/mechanism in current stored Core Memory usage. Do not rewrite direction
  during normalization.
- `led_to`: a process/progression edge, not generic causal proof.
- `blocked_by`: the source bead is prevented by the target blocker in current
  stored Core Memory usage.
- `unblocks`: the source removes a blocking condition for the target.
- `blocks_unblocks`: legacy compound relation. It remains accepted for
  compatibility but is discouraged for new model-authored associations unless
  the transition itself is the object being represented.

Unknown semantics should use `associated_with` or quarantine/review. Do not use
`supports` as a fallback for unclear semantics.

Accepted aliases normalize spelling variants such as `causes`, `leads_to`,
`blocks`, `unblocked`, `enabled`, `conflicts_with`, `related_to`, and
`blocks->unblocks` to existing canonical values. Alias normalization never
rewrites source/target direction.

## Derived/helper relation tags

Defined in `core_memory/schema/normalization.py` (`DERIVED_RELATION_TYPES`):
- related
- shared_tag

Rule:
- derived/helper tags are not equivalent to canonical structural relations.

## Canonical operational statuses

Defined in `core_memory/schema/normalization.py` (`CANONICAL_BEAD_STATUSES`):
- open
- candidate
- promoted
- archived
- compacted
- superseded

## Normalization entrypoints

Defined in `core_memory/schema/normalization.py`:
- `normalize_bead_type(...)`
- `is_allowed_bead_type(...)`
- `normalize_relation_type(...)`
- `relation_kind(...)`
- `relation_family(...)`
- `is_causal_relation(...)`
- `is_evidential_relation(...)`

## Compatibility policy

- Legacy inputs remain accepted where currently required for compatibility.
- Canonical internal values should be emitted by current write paths.
- No storage migration is required in Phase T1.
