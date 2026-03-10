# WORKFLOW_AUTO.md

## Phase 1 Baseline (enabled)

On automation/heartbeat cycles:

1. Ensure `memory/YYYY-MM-DD.md` exists for today (create with a header if missing).
2. Ensure `memory/heartbeat-state.json` exists (initialize if missing).
3. Run Core Memory health check:
   - `.venv/bin/core-memory --root /home/node/.openclaw/workspace/memory stats`
   - Stay silent unless this fails.
4. Refresh rolling context on each automation cycle (dynamic budget):
   - `python3 /home/node/.openclaw/workspace/scripts/consolidate.py rolling-window --token-budget 2000 --max-beads 200`
   - Stay silent unless this fails.

## Phase 2 Session-End Extraction (enabled)

On session-end / memoryFlush:

1. Run extraction + consolidation:
   - `python3 /home/node/.openclaw/workspace/extract-beads.py <session-id> --consolidate`
2. If `<session-id>` is unavailable, run:
   - `python3 /home/node/.openclaw/workspace/extract-beads.py --consolidate`
3. Idempotency is enforced by extraction markers under:
   - `<CORE_MEMORY_ROOT>/.beads/.extracted/session-<id>.json`
4. Stay silent unless extraction/consolidation fails.

## Deferred (not yet enabled)
- Association crawler automation (blocked on legacy script migration).
