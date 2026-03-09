# V2-P6A Kickoff (Authority Cutover)

Status: Active
Purpose: decisive authority cutover to target architecture.

## Objective
Move runtime/storage authority explicitly toward target architecture:
- session-first live authority
- memory-engine-owned orchestration
- canonical association/edge sourcing for retrieval catalog semantics
- explicit rolling continuity surface ownership

## Step plan (5)
1. Session-first live authority cutover foundation ✅
2. Memory engine orchestration ownership deepening
3. Retrieval catalog relation sourcing correction (canonical associations)
4. Rolling surface ownership tightening
5. Full sweep + P6A closeout gate

## Guardrails
- Preserve `memory.execute/search/reason` contracts.
- Mainline path must remain operational after each step.
- Add-before-remove for migration-safe cutover.

## Step 1 completion notes
- Added explicit session-first live authority reader module:
  - `core_memory/live_session.py`
  - `read_live_session_beads(root, session_id)`
- Authority policy implemented for live reads:
  - primary: session surface (`session-s<id>.jsonl`)
  - fallback: index projection only when session surface unavailable/empty
- Exposed live session read through runtime center:
  - `core_memory/memory_engine.py::read_live_session(...)`
- Added regression coverage:
  - `tests/test_live_session_authority.py`
  - validates session-surface-first behavior and deterministic fallback path
