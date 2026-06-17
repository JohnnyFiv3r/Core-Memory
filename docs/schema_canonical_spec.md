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
- causes, leads_to, blocks, unblocks, blocks_unblocks
- supersedes, refines
- associated_with, contradicts, invalidates, diagnoses
- applies_pattern_of, constraint_transformed_into
- similar_pattern, generalizes, reveals_bias
- supports, derived_from, part_of, resolves, precedes, enables

## Relation families and direction rules

Shared helper families are defined in `core_memory/schema/normalization.py` so
Myelination, Assembly Depth, retrieval reporting, and agent guidance do not each
carry private relation sets.

Families are helper classifications only. They do not migrate stored rows or
change graph traversal semantics.

- causal: causes, leads_to, resolves, diagnoses
- evidence: supports, derived_from, causes, leads_to, resolves
- influence: blocks, unblocks, blocks_unblocks, enables
- conflict: contradicts, invalidates
- temporal: precedes
- revision: supersedes, refines

Direction rules for new authored associations:
- `causes`: the source bead is evidence/cause for the affected target bead.
- `leads_to`: a process/progression edge, not generic causal proof.
- `blocks`: the source bead blocks or prevents the target bead.
- `unblocks`: the source removes a blocking condition for the target.
- `blocks_unblocks`: legacy compound relation. It remains accepted for
  compatibility but is discouraged for new model-authored associations unless
  the transition itself is the object being represented.

Unknown semantics should use `associated_with` or quarantine/review. Do not use
`supports` as a fallback for unclear semantics.

Accepted aliases normalize spelling variants and retired synonym labels such as
`caused_by`, `led_to`, `unblocked`, `enabled`, `conflicts_with`, `related_to`,
`reinforces`, `mirrors`, `structural_symmetry`, `solves_same_mechanism`,
`transferable_lesson`, `violates_pattern_of`, and `blocks->unblocks` to
existing canonical values.

Inverse labels `blocked_by`, `superseded_by`, `follows`, and `specializes`
normalize to active canonical labels and require endpoint swapping at
association write boundaries. Use `core-memory ops canonicalize-relations` for
dry-run inspection and `core-memory ops canonicalize-relations --apply` to
rewrite existing active index rows with audit events.

Dreamer may preserve retired pattern labels as `relationship_signal` metadata
for candidate-family evaluation, but persisted graph edges use canonical
relation labels.

New model-authored associations should prefer the active canonical labels and
source -> target direction directly.

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
