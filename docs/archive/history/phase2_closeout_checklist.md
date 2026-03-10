# Phase 2 Closeout Checklist

Status: Canonical transition checklist
Related roadmap: `docs/transition_roadmap_locked.md`

## Goal
Confirm Phase T1 (schema normalization) is complete before write-side trigger-model correction and internalization phases.

## Checklist

### Inventory and spec
- [x] Baseline inventory captured: `docs/schema_inventory_baseline.md`
- [x] Canonical schema spec defined: `docs/schema_canonical_spec.md`

### Code-level normalization
- [x] Canonical schema helpers added: `core_memory/schema.py`
- [x] Legacy bead alias normalization present (`promoted_*` -> canonical)
- [x] Relationship taxonomy split represented (canonical vs derived)

### Compatibility
- [x] Legacy alias inputs remain accepted via normalization
- [x] No root script path changes
- [x] No artifact path changes
- [x] No endpoint contract changes

### Tests
- [x] Schema normalization tests added and passing (`tests/test_schema_normalization.py`)
- [x] Existing critical ingress/runtime tests pass

## Exit criteria
Phase 2 is considered complete when canonical schema layers are explicit, normalization is active, and compatibility is preserved.

## Next phase
Proceed to Phase T3:
- write-side trigger model correction (event-native convergence)
