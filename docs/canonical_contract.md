# Canonical Contract (Product Owner View)

This document is the single source of truth for what **must never break**.

## A) Per-turn path (`agent_end`)
For every finalized turn:
1. Ingest turn idempotently (`session_id + turn_id`)
2. Write one turn bead (current-turn memory)
3. Append relevant in-session associations
4. Evaluate promotion state for all visible session beads
   - states: `promoted | candidate | null`
   - `promoted` is irreversible

## B) Flush path (memory flush cycle only)
1. Archive phase
2. Compaction phase (non-promoted only)
3. Rolling-window maintenance write
4. Flush cycle checkpoint/write report
5. Second flush in same cycle must skip

## C) Rolling window maintenance
- Rolling window is updated during flush sequence.
- Rolling write is not a turn-path side effect.
- Phase trace must include `rolling_window_write`.

## D) Archive ergonomics
- Flush path must include both:
  - `archive_compact_session`
  - `archive_compact_historical`
- Flush report artifacts are written for committed/failed/skipped outcomes.

## E) Full retrieval path
- Canonical retrieval path remains callable end-to-end through memory tool execution.
- Health checks should validate retrieval returns structured output after turn+flush.

## F) Operator checks
Use CLI:
- `core-memory metrics canonical-health`
- `core-memory metrics legacy-readiness`

A release is considered safe only if canonical health is green and legacy readiness matches expected cutover policy.
