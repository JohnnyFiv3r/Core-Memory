# V2-P7A Kickoff (Authority Completion)

Status: Active

## Step plan (5)
1. Session-write authority cutover foundation ✅
2. Engine-first orchestration ownership expansion ✅
3. Session-authority propagation into key write/read paths ✅
4. Index projection demotion hardening
5. Full sweep + P7A closeout

## Step 1 completion notes
- Updated store architecture posture to session-first live authority + index projection/cache semantics.
- Strengthened write-time association candidate sourcing to use session surface first, then index projection fallback.
- Added regression test proving association pass can still resolve from session surface even if index projection is stale/missing row.

## Step 2 completion notes
- Expanded memory-engine ownership of integration orchestration paths:
  - added `emit_turn_finalized(...)` in `core_memory/memory_engine.py`
  - added `process_pending_legacy_events(...)` in `core_memory/memory_engine.py`
- Converted OpenClaw integration functions to wrappers that delegate to memory engine:
  - `coordinator_finalize_hook(...)` -> engine emit path
  - `process_pending_memory_events(...)` -> engine legacy-poller path
- Result: integration adapters are thinner, and runtime orchestration ownership shifts further into `memory_engine.py`.

## Step 3 completion notes
- Propagated session-authority semantics into key query/read path in `MemoryStore.query(...)`:
  - new `session_id` filter now uses session surface first
  - index projection used only as fallback if session surface unavailable
- Added regression coverage:
  - `tests/test_session_first_query_authority.py`
  - verifies session query remains correct even when index projection is stale
