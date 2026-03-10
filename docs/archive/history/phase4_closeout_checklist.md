# Phase 4 Closeout Checklist

Status: Canonical transition checklist
Related roadmap: `docs/transition_roadmap_locked.md`

## Goal
Confirm Phase T4 (write-side internalization) is complete while preserving root-script compatibility contracts.

## Checklist

### Internal modules
- [x] write-side internal module graph exists under `core_memory/write_pipeline/`
- [x] transcript discovery and marker parsing internalized
- [x] normalization/persistence/idempotency internalized
- [x] rolling-window and consolidation internalized

### Wrapper preservation
- [x] `extract-beads.py` remains callable at root path
- [x] `consolidate.py` remains callable at root path
- [x] script CLI command/flag shapes preserved
- [x] script paths unchanged

### Artifact/path contracts
- [x] extraction marker path unchanged
- [x] rolling-window artifact path (`promoted-context.md`) unchanged

### Trigger model compatibility
- [x] trigger emission preserved with dispatch loop guard
- [x] trigger dispatch remains functional and idempotent

### Parity/validation
- [x] extraction parity tests pass
- [x] consolidation output contract tests pass
- [x] runtime/ingress/schema regression tests pass

## Exit criteria
Phase 4 is complete when write-side business logic is owned by canonical internal modules and root scripts operate as compatibility wrappers without behavior regressions.

## Next phase
Proceed to Phase T5:
- memory surface explicitness and truth hierarchy clarity consolidation.
