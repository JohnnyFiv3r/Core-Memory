# V2 Invariants

Status: Canonical (V2-P1)
Related: `docs/v2_execution_plan.md`, `docs/transition_roadmap_v2.md`

## Purpose
Define non-negotiable architectural and behavioral invariants for V2 execution.

## Core invariants

1. **Mainline stability invariant**
   - Mainline path must remain operational at every phase boundary.

2. **Core Memory isolation invariant**
   - Core Memory must not read, write, index, or depend on OpenClaw `MEMORY.md`.

3. **Tool contract invariant**
   - `memory.execute`, `memory.search`, and `memory.reason` contracts cannot drift silently.

4. **Session write invariant**
   - Session memory is append-only during session lifetime (until flush boundary).

5. **Archive write boundary invariant**
   - Archive writes occur at memory flush only (not normal turn writes).

6. **Archive fidelity invariant**
   - Archive stores full-fidelity beads (no rolling-style compression in archive copy).

7. **Rolling window invariant**
   - Rolling window is a continuity projection, built at flush, strict recency FIFO under token budget.

8. **Compression scope invariant**
   - Compression of non-promoted beads applies to rolling copy only, not archive copy.

9. **ID continuity invariant**
   - Representations across session/archive/rolling preserve bead IDs.

10. **Trigger authority invariant**
   - Flush hook is authoritative orchestration boundary; sidecar behavior is legacy compatibility during migration.

11. **Idempotency invariant**
   - Trigger processing and flush staging must be retry-safe and idempotent.

12. **Determinism invariant (read-side)**
   - Ranking/order/confidence-next behavior remains deterministic and testable.
