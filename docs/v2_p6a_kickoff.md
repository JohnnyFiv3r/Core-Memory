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
2. Memory engine orchestration ownership deepening ✅
3. Retrieval catalog relation sourcing correction (canonical associations) ✅
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

## Step 2 completion notes
- Deepened runtime center ownership in `core_memory/memory_engine.py`:
  - engine now performs turn-finalized request normalization/defaulting
  - auto-generates transaction/trace ids when absent
  - emits engine metadata markers in outputs (`engine.entry`, `engine.normalized`)
- Added flush preflight context attachment via engine:
  - records live-session authority snapshot in flush output (`engine.live_session_*`)
- Updated memory engine regression tests to validate engine-owned metadata behavior

## Step 3 completion notes
- Corrected retrieval catalog relation sourcing in `core_memory/memory_skill/catalog.py`
  - primary source now: canonical association records (`index.associations[*].relationship`)
  - transitional fallback to bead-local links only when no association relations present
- Added regression coverage:
  - `tests/test_catalog_relation_source.py`
  - verifies relation types are populated from canonical association records
