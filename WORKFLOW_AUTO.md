# WORKFLOW_AUTO.md

## Phase 1 Baseline (enabled)

On automation/heartbeat cycles:

1. Ensure `memory/YYYY-MM-DD.md` exists for today (create with a header if missing).
2. Ensure `memory/heartbeat-state.json` exists (initialize if missing).
3. Run Core Memory health check:
   - `.venv/bin/core-memory --root /home/node/.openclaw/workspace/memory stats`
   - Stay silent unless this fails.
4. Refresh rolling context once per day:
   - `python3 /home/node/.openclaw/workspace/consolidate.py rolling-window --limit 20`
   - Stay silent unless this fails.

## Deferred (not yet enabled)
- Session-end extraction idempotency rules.
- Association crawler automation (blocked on legacy script migration).
