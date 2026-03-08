# Core Memory V2 Execution Plan

Status: Draft for approval
Related: `docs/transition_roadmap_v2.md`

## Execution posture

- Single-user refactor program (not a multi-tenant/public release track).
- Compatibility priority is limited to **mainline path stability**.
- Legacy paths may remain temporarily during migration, then are explicitly deprecated/removed after cutover.

## Global guardrails

1. Mainline path must remain operational at each phase boundary.
2. Core Memory remains isolated from OpenClaw built-in memory (`MEMORY.md`).
3. `memory.execute`, `memory.search`, `memory.reason` contracts must not silently drift.
4. Add-before-remove during migration; remove legacy only after canonical path is verified.

---

## V2-P1 — Canonical spec lock

### Goal
Freeze architecture semantics and deprecation targets before further refactor.

### Deliverables
- `docs/v2_invariants.md`
- `docs/v2_flush_transaction_spec.md`
- `docs/v2_surface_authority_matrix.md`
- `docs/v2_deprecation_inventory.md`

### Acceptance
- Surfaces/authority/flush semantics approved.
- Legacy inventory is explicit (what will be removed, when, and preconditions).

---

## V2-P2 — Event-native authority cutover

### Goal
Make write-side trigger enforcement canonical and in-process.

### Hard requirement
Per-turn event hooks currently implemented via sidecar semantics must be integrated into the canonical system path and enforced, not merely documented/emitted.

### Scope
- Promote memory flush hook path as authoritative orchestration boundary.
- Integrate documented per-turn trigger set into canonical in-process execution path.
- Keep sidecar/event poller behavior as temporary legacy compatibility only.
- Add manual admin flush trigger CLI fail-safe.

### Acceptance
1. Documented per-turn triggers are implemented in canonical path.
2. Trigger enforcement is deterministic and idempotent.
3. Sidecar path is marked legacy compatibility, not authority.
4. Mainline tests verify per-turn trigger firing and outcomes.

---

## V2-P3 — Transactional flush implementation

### Goal
Implement staged, retry-safe flush protocol.

### Stages
1. Ensure final-turn enrichment completed.
2. Persist full-fidelity session beads to archive.
3. Build/write rolling window projection (compression policy only here).
4. Write checkpoint/commit marker.

### Acceptance
- Crash/retry tests pass.
- No duplication/corruption under replay.
- Mainline flow remains stable.

---

## V2-P4 — Storage role hardening

### Goal
Enforce storage boundaries in code.

### Rules to enforce
- No archive writes during normal turns.
- Archive writes only at flush.
- Session file append-only until flush.
- Rolling window strict recency FIFO (~10k tokens), no promotion priority override.
- Same bead IDs preserved across represented surfaces.

### Acceptance
- Boundary invariant tests pass.
- Violations fail loudly with explicit diagnostics.

---

## V2-P5 — Mainline simplification + legacy deprecation

### Goal
Retire legacy paths after canonical flow is proven.

### Scope
- Deprecate and remove legacy orchestration/write paths from active flow.
- Keep only canonical path + minimal admin fail-safe controls.
- Update docs to describe canonical path first; legacy notes explicitly marked deprecated.

### Acceptance
- Deprecation inventory resolved or explicitly deferred with rationale.
- No unresolved hidden legacy authority paths.

---

## V2-P6 — Final verification + closeout

### Goal
Confirm V2 readiness and complete cutover.

### Validation set
- Full regression suite
- Eval suite (`memory_execute_eval`, paraphrase checks)
- End-to-end operator sanity run
- Closeout checklist + risk log review

### Acceptance
- Mainline path stable.
- V2 docs marked canonical.
- Legacy removals/deprecations recorded.

---

## Deprecation inventory template

Use this schema in `docs/v2_deprecation_inventory.md`:

- Component/path:
- Current role:
- Why legacy:
- Canonical replacement:
- Removal preconditions:
- Planned phase:
- Status: (active / deprecated / removed)
- Notes:

---

## Immediate next step

Start V2-P1 with docs/invariant artifacts only, then pause for approval before any implementation changes.
