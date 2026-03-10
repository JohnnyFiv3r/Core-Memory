# Core Memory Transition Roadmap v2 (Superseding)

Status: Draft for review
Supersedes: `docs/transition_roadmap_locked.md` for future execution phases after approval
Purpose: align transition sequencing to event-native write-side foundations, explicit storage roles, and read/write boundary clarity.

---

## 1) Why v2

The prior roadmap delivered major stabilization (T1-T6), but v2 clarifies the intended architecture:

- indexing/write-side should be as clean and canonical as retrieval/read-side
- event triggers must be foundational (not sidecar-ish in authority)
- storage mechanisms must have explicit, enforced roles

---

## 2) Canonical storage and surface roles

## A. Session memory file (authoritative active write surface)
- One append-only file per session.
- Full-fidelity bead writes during session.
- Per-turn enrichment updates (promotion assessment, semantic tags, causal associations).
- No archive writes during normal turns.

## B. Archive graph/store (authoritative durable retrieval surface)
- Receives full-fidelity beads from session file at memory flush.
- Archive is not compaction output; it is the durable DB used by retrieval tools.
- Same bead IDs retained across session -> archive transfer.

## C. Rolling window file (authoritative continuity injection surface)
- Built/updated at memory flush only.
- Source is current session memory.
- Non-promoted beads are compressed in rolling copies only.
- Strict recency FIFO with token budget (~10k).
- No promotion/causal priority override in window selection.

## D. Transcript
- Immediate session-local wording truth.

## E. MEMORY.md (OpenClaw semantic surface)
- Parallel OpenClaw semantic memory layer.
- Not canonical Core Memory bead/archive truth source.

---

## 3) Truth hierarchy

For agent runtime interpretation:

1. Active session memory
   - transcript first
   - then same-session beads
2. Rolling window injected context
3. Archive graph via retrieval tools (`memory.execute`, `memory.search`, `memory.reason`)

Archive is the deep/full context truth when rolling entries are compressed.

---

## 4) Trigger model (authoritative)

- Authoritative write-side trigger: memory flush hook.
- Manual admin flush trigger must exist in CLI as fail-safe.

Per-turn crawler work remains required for enrichment quality before flush finalization.

---

## 5) Flush transaction safety model

Flush should follow an idempotent staged protocol:

1. Ensure association crawler has processed final turn.
2. Persist full-fidelity session beads to archive.
3. Build/update rolling window projection from session surface.
4. Write success checkpoint/state marker.

Requirements:
- retry-safe idempotency
- partial failure recovery without duplication/corruption
- deterministic replay behavior

---

## 6) Tooling boundary (unchanged)

Agent-facing tool surface remains:
- `memory.execute` (default runtime facade)
- `memory.search` (typed retrieval)
- `memory.reason` (causal reasoning)

`core_memory/tools/memory.py` remains adapter layer only.

---

## 7) Proposed v2 phase map

## V2-P1 — Canonical role/spec lock
- Publish canonical spec docs for surfaces, truth hierarchy, flush transaction model.
- Freeze naming and ownership rules.

Exit:
- docs accepted as canonical
- ambiguity removed for write/read/storage roles

## V2-P2 — Event-native write authority
- Promote memory flush trigger path to first-class authoritative orchestration boundary.
- Ensure sidecar semantics become implementation detail, not authority model.

Exit:
- event authority explicit in code and docs
- compatibility wrappers preserved

## V2-P3 — Session->archive transactionalization
- Implement staged, idempotent flush commit protocol.
- Add checkpoint/replay safety tests.

Exit:
- deterministic idempotent flush under retries/failures

## V2-P4 — Rolling window canonicalization
- Enforce strict recency FIFO token policy.
- Ensure rolling compression policy is isolated to rolling surface only.

Exit:
- deterministic rolling behavior under budget pressure

## V2-P5 — Boundary hardening + operator controls
- Add/validate manual admin flush CLI fail-safe.
- Harden observability, diagnostics, and invariants.

Exit:
- operational confidence with clear recovery playbooks

---

## 8) Non-negotiables

- No silent contract drift for `memory.execute` / `memory.search` / `memory.reason`.
- Archive remains full-fidelity durable source.
- Rolling window remains continuity projection and may contain compressed non-promoted copies.
- Same bead IDs preserved across surfaces where represented.
- Add before remove; compatibility wrappers remain until explicit cutover.
- Core Memory is fully isolated from OpenClaw built-in memory surfaces.
- Core Memory must not read, write, index, or depend on `MEMORY.md`.
- OpenClaw default memory system remains parallel/semantic-first and complementary, not an execution dependency of Core Memory.

---

## 9) Immediate next step

Start with **V2-P1 kickoff plan** (docs + invariant matrix + acceptance tests definition), then pause for review before code execution phases.
