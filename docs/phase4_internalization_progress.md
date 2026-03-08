# Phase 4 Internalization Progress

Status: In Progress
Related roadmap: `docs/transition_roadmap_locked.md` (Phase T4)

## Objective
Internalize write-side business logic into canonical internal modules while preserving root script entrypoints and contracts.

## Implemented in this slice

### Internal write-pipeline modules created
- `core_memory/write_pipeline/transcript_source.py`
- `core_memory/write_pipeline/marker_parse.py`
- `core_memory/write_pipeline/normalize.py`
- `core_memory/write_pipeline/persist.py`
- `core_memory/write_pipeline/idempotency.py`
- `core_memory/write_pipeline/window.py`
- `core_memory/write_pipeline/consolidate.py`
- `core_memory/write_pipeline/orchestrate.py`

### Root script delegation progress
- `extract-beads.py` now delegates extraction orchestration to `run_extract_pipeline(...)`
- `consolidate.py` command branches now delegate to internal consolidate/window pipeline functions

### Compatibility constraints preserved
- root script paths unchanged
- CLI command names unchanged
- artifact path `promoted-context.md` unchanged
- trigger emission behavior preserved with dispatch loop guards

## Validation
- `tests.test_write_triggers` passing
- `tests.test_write_trigger_dispatch` passing
- `tests.test_write_pipeline_internalization` passing
- `tests.test_http_ingress` passing
- `tests.test_memory_execute_contract` passing

## Remaining T4 work
- expand extraction parity fixtures/tests
- tighten consolidation parity assertions vs prior behavior output details
- reduce duplicate dead code from root scripts after parity confidence is reached
- add phase 4 closeout checklist once parity set is complete
