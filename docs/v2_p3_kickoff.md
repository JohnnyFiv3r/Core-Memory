# V2-P3 Kickoff

Status: Active
Related: `docs/v2_phase_ticket_map.md`, `docs/v2_gap_checklist.md`

## Objective
Implement transactionalization + authority hardening workstream.

## Step plan (5)
1. Canonical runtime center module definition ✅
2. Session authority cutover groundwork ✅
3. Enrichment barrier strict enforcement before flush ✅
4. Replay/idempotency hardening for trigger paths ✅
5. Flush stage failure-injection + resume behavior validation

## Step 1 completion notes
- Added canonical runtime center module: `core_memory/memory_engine.py`
- Routed turn-finalized integration path through memory engine entrypoint
- Routed flush CLI path through memory engine entrypoint
- Added regression tests: `tests/test_memory_engine.py`

## Step 2 completion notes
- Added explicit session surface module: `core_memory/session_surface.py`
- Added `read_session_surface(...)` for append-only session file reads (`.beads/session-<id>.jsonl`)
- Flush pipeline now records session-surface authority context in checkpoint start stage:
  - `session_surface: session_file`
  - `session_bead_count`
- Added regression coverage: `tests/test_session_surface.py`
- Extended flush checkpoint test to assert session-surface marker

## Step 3 completion notes
- Implemented strict enrichment barrier in `run_flush_pipeline(...)` (default on via `CORE_MEMORY_ENFORCE_ENRICHMENT_BARRIER=1`)
- Flush now checks latest session turn memory-pass status before progressing
- If barrier not satisfied, flush fails deterministically with:
  - `error: enrichment_barrier_not_satisfied`
  - failed checkpoints for `enrichment_ready` and overall `failed`
- Added regression coverage in `tests/test_trigger_orchestrator_flush.py`:
  - fails when latest turn is emitted but not processed
  - passes after finalize+process path completes

## Step 4 completion notes
- Added flush transaction idempotency state file: `.beads/events/flush-state.json`
- Added lock-protected flush claim/mark flow in trigger orchestrator:
  - `_claim_flush_tx(...)`
  - `_mark_flush_tx(...)`
- Flush replays with same `flush_tx_id` now skip safely with deterministic reason:
  - `already_committed` or `already_running`
- Added regression coverage for same-tx replay skip in `tests/test_trigger_orchestrator_flush.py`
