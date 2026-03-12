# Core Memory — Full Implementation Plan (Write-Side Contract)

Date: 2026-03-12
Context: enforce canonical event-driven behavior from architecture diagram

## Objective
Implement deterministic, session-scoped memory behavior:

Per `agent_end` turn:
1. Write current-turn bead
2. Append relevant associations across visible in-session beads
3. Decide state for visible in-session beads: `promoted | candidate | null`
   - Promotion is irreversible (no unpromote path)

Per flush cycle (not per turn):
4. Archive full bead + association snapshots
5. Compact non-promoted beads only
6. Write rolling-window store once per session flush cycle

---

## A) Contracts and Invariants

### A1. Invariants (must hold)
- Idempotent turn processing by `(session_id, turn_id)`
- Exactly one canonical turn bead write per successful new turn
- Association appends are deterministic and bounded
- Promotion monotonicity: `promoted` is terminal
- No compaction on turn path
- Flush path order: archive -> compact -> rolling window write
- Rolling-window write occurs only on flush

### A2. State model
For each bead, normalize to:
- `promotion_state`: `promoted | candidate | null`
- `promotion_locked`: bool (true once promoted)
- `promotion_decision_at`: ISO timestamp
- `promotion_decision_turn_id`: turn source
- `promotion_evidence`: structured object (required for promote)

Compatibility:
- legacy `status` fields map to new state without data loss

---

## B) Turn Path Implementation (`agent_end`)

### B1. Entry and idempotency
Owner: `memory_engine.process_turn_finalized`
- Keep current emit + claim semantics
- Early-exit on duplicate turn claim

### B2. Bead creation
- Ensure one canonical bead for current turn
- Required metadata:
  - session_id, turn_id, source_turn_ids
  - type/title/summary/detail

### B3. Association append pass
- Build visible bead set from current session authority surface
- Compute relevant links for:
  - new bead -> visible beads
  - optional visible<->visible updates when triggered by current turn
- Stable sort + bounded append
- Persist via append-only side log then projection merge

### B4. Promotion decision pass
- Iterate all visible session beads each turn
- Decision output per bead: `promoted | candidate | null`
- If already promoted/locked: skip mutation
- Require strong evidence object for promotion
- Persist decision event + bead fields

### B5. Per-turn metrics
Emit deterministic turn metrics:
- beads_created
- associations_appended
- decisions_promoted
- decisions_candidate
- decisions_null
- promoted_locked_skips

---

## C) Flush Path Implementation

### C1. Trigger semantics
Owner: `memory_engine.process_flush`
- Run only at memory flush/compaction trigger
- enforce once-per-session-cycle checkpoint

### C2. Archive phase
- Write full snapshots of affected beads and association projection state
- append-only, revisioned

### C3. Compaction phase
- Compact only non-promoted beads
- promoted beads preserve full detail

### C4. Rolling-window phase
- Build rolling window from post-compaction state
- write exactly once for the cycle
- persist cycle metadata (`flush_cycle_id`, `last_flush_*`)

### C5. Flush metrics/checkpoints
- archived_count
- compacted_count
- rolling_selected_count
- cycle_id + completion marker

---

## D) Module-Level Change Plan

### D1. `core_memory/memory_engine.py`
- enforce strict sequencing for turn and flush
- add cycle checkpoint guards for flush

### D2. `core_memory/association/crawler_contract.py`
- deterministic association normalization/appends
- session visibility gates

### D3. `core_memory/store.py` (or extracted policy module)
- add monotonic promotion lock enforcement
- normalize legacy state mapping

### D4. `core_memory/write_pipeline/consolidate.py`
- guarantee archive->compact->rolling ordering
- remove/guard any paths that compact outside flush context

### D5. Plugin/bridge integration
- once bridge is stable, ensure `agent_end` invokes turn path
- flush hooks call flush path only (no turn compaction)

---

## E) Test Matrix (Required)

### E1. Turn-path tests
1. single turn creates bead exactly once
2. replay same turn does not duplicate
3. associations appended deterministically
4. decision pass visits all visible session beads

### E2. Promotion invariants
5. promoted bead cannot transition back
6. promotion requires evidence payload
7. candidate/null can change until promoted

### E3. Flush tests
8. flush archives before compaction
9. compact excludes promoted beads
10. rolling-window written once per cycle
11. second flush in same cycle is guarded no-op

### E4. Regression/compat
12. legacy bead status read compatibility
13. retrieval outputs remain deterministic
14. no behavior break on existing tool surfaces

---

## F) Delivery Plan (PR stack)

### PR-1: Contract + schema + docs
- add invariant docs
- add normalized promotion fields
- compatibility mapping only

### PR-2: Turn-path enforcement
- per-turn bead + associations + decisions
- metrics events

### PR-3: Flush-cycle enforcement
- cycle checkpoints
- archive->compact->rolling hard order

### PR-4: Tests + diagnostics
- complete matrix above
- debug report command(s)

### PR-5: Legacy cleanup
- retire conflicting promotion branches
- tighten authority-path guards

---

## G) Operational Rollout

1. Gate with env flag initially (safe rollout)
2. Run side-by-side diagnostics for real sessions
3. Verify no duplicate writes, no demotions, correct flush cadence
4. Promote to default behavior
5. remove legacy toggles

---

## H) Known Risks + Mitigations

- Risk: hidden legacy paths reintroduce demotion
  - Mitigation: central promotion-lock check + tests

- Risk: over-linking associations
  - Mitigation: bounded candidate set + deterministic ordering

- Risk: flush running too often
  - Mitigation: cycle checkpoints + idempotent flush markers

- Risk: bridge instability (current issue)
  - Mitigation: keep bridge disabled until hardened/native hook path lands

---

## I) Immediate Next Step

Start PR-1 from `feat/pr01-scaffolding-bootstrap` with:
- contract doc + state schema
- monotonic promotion guard code path
- tests for irreversible promotion invariant
