# V2-P15 Kickoff (Worker Mechanical-Only Cut)

Status: Active

## Objective
Remove residual deterministic semantic judgment behavior from worker runtime path so crawler-reviewed flow is the sole canonical semantic authority.

## Step plan (3)
1. Worker mechanical-only cut ✅
2. Crawler handoff enforcement
3. Sweep + closeout

## Step 1 completion notes
- Reworked `core_memory.sidecar_worker.process_memory_event(...)` to mechanical/bookkeeping-only behavior.
- Removed deterministic semantic judgment responsibilities from worker path:
  - no deterministic bead creation decisions
  - no deterministic promotion decisions
  - no deterministic association decisions
- Worker now handles envelope normalization, pass bookkeeping, and metrics only.
- Updated worker/authority tests to enforce zero semantic mutation in worker path.
