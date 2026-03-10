# Phase 3 Closeout Checklist

Status: Canonical transition checklist
Related roadmap: `docs/transition_roadmap_locked.md`

## Goal
Confirm Phase T3 (write-side trigger model correction) reached safe event-native convergence milestones without breaking compatibility.

## Checklist

### Trigger instrumentation
- [x] canonical write-trigger event stream exists (`core_memory/write_triggers.py`)
- [x] compatibility scripts emit trigger events (`extract-beads.py`, `consolidate.py`)

### Trigger dispatch path
- [x] explicit trigger dispatch path exists (`dispatch_write_trigger`)
- [x] dispatch can execute compatibility script entrypoints via trigger type
- [x] processed-trigger idempotency marker exists (`write-trigger-processed.jsonl`)

### Compatibility safety
- [x] root script paths unchanged
- [x] root script CLI shapes unchanged
- [x] artifact paths unchanged
- [x] loop prevention guard exists (`CORE_MEMORY_TRIGGER_DISPATCH=1` skips re-emit)

### Validation
- [x] trigger emit tests pass
- [x] trigger dispatch tests pass
- [x] ingress/runtime regression tests remain passing

## Exit criteria
Phase 3 is considered complete when write-side trigger handling is explicit, dispatchable, idempotent, and compatibility-preserving.

## Next phase
Proceed to Phase T4:
- write-side internalization (move business logic into canonical internal modules while preserving wrapper entrypoints)
