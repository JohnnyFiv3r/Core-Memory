# V2 Gap Checklist (Realized / Partial / Missing / Misaligned)

Status: Canonical assessment artifact
Purpose: translate current architecture state into concrete implementation priorities for V2-P3/P4/P5.

## Legend
- **Realized**: architecture intent is materially embodied in code paths.
- **Partial**: direction exists but center-of-gravity still transitional.
- **Missing**: capability not yet embodied as required.
- **Misaligned**: implementation exists but contradicts intended architecture shape.

---

## 1) Canonical runtime center (`memory_engine.py` or equivalent)
Status: **Partial**

Evidence:
- `core_memory/trigger_orchestrator.py` (new canonical entry for turn/flush orchestration)
- Runtime concerns still spread across:
  - `core_memory/integrations/api.py`
  - `core_memory/openclaw_integration.py`
  - `core_memory/sidecar_worker.py`
  - `core_memory/write_pipeline/orchestrate.py`

Gap:
- no single obvious runtime engine module owning full orchestration semantics.

Mapped phase/ticket:
- **V2-P3-T1**: Introduce canonical runtime engine module (or promote `trigger_orchestrator.py` to this role explicitly).

---

## 2) Session memory file as live authoritative structured surface
Status: **Partial / Misaligned**

Evidence:
- Session write paths and extraction exist.
- `core_memory/store.py` + `.beads/index.json` still act as primary index-first truth in many flows.

Gap:
- live authority still too index-centric rather than session-surface-first.

Mapped phase/ticket:
- **V2-P3-T2**: Session authority cutover: session file becomes live authority until flush checkpoint.

---

## 3) Event triggers as foundational (not sidecar authority)
Status: **Partial**

Evidence:
- Canonical in-process orchestration added (`trigger_orchestrator.py`).
- Sidecar path explicitly marked legacy compatibility (`authority_path=legacy_sidecar_compat`).
- P2 enforcement tests added.

Gap:
- Strict enrichment barrier + full staged retry semantics still need hardening.

Mapped phase/ticket:
- **V2-P3-T3**: enforce hard per-turn enrichment barrier before flush stage progression.
- **V2-P3-T4**: failure/replay hardening under induced stage failures.

---

## 4) Rolling window as first-class continuity store surface
Status: **Partial**

Evidence:
- Rolling build logic exists (`core_memory/write_pipeline/window.py`, consolidate paths).
- Artifact written (`promoted-context.md`) with deterministic policy docs.

Gap:
- still feels primarily rendered artifact-centric; explicit rolling store contract can be stronger.

Mapped phase/ticket:
- **V2-P4-T1**: formal rolling store contract + projection metadata as first-class surface semantics.
- **V2-P4-T2**: strict recency FIFO property tests under budget pressure.

---

## 5) Association subsystem as explicit package/pass
Status: **Missing / Partial**

Evidence:
- Association semantics exist but are dispersed:
  - `store.py`, `graph.py`, `sidecar_worker.py`, relation maps.

Gap:
- no single association subsystem with clear pass boundaries and invariants.

Mapped phase/ticket:
- **V2-P4-T3**: create explicit `core_memory/association/` subsystem (pass orchestration, causal maintenance, promotion-shaping hooks).

---

## 6) Schema enforcement completion (`schema.py` vs models drift)
Status: **Partial / Misaligned**

Evidence:
- canonical schema module exists (`core_memory/schema.py`).
- drift remains across older enums/legacy model surfaces (`core_memory/models.py` etc.).

Gap:
- incomplete enforcement of canonical noun system across all runtime/model entrypoints.

Mapped phase/ticket:
- **V2-P4-T4**: schema reconciliation sweep (`schema.py` authoritative; models aligned).

---

## 7) “association” as bead type decision
Status: **Misaligned (pending decision closure)**

Evidence:
- `association` still present in type surfaces in parts of implementation.

Gap:
- unresolved architectural decision on bead-type vs edge-type role.

Mapped phase/ticket:
- **V2-P4-T5**: explicit ADR/decision + migration action (keep/remove/reclassify).

---

## 8) SpringAI bridge framing vs generic ingress
Status: **Partial**

Evidence:
- runtime HTTP ingress exists and works.
- framing still generic-first in parts of integration layout.

Gap:
- naming/contract posture not fully aligned to SpringAI-specific bridge intent.

Mapped phase/ticket:
- **V2-P5-T1**: integration framing cleanup (SpringAI-bridge-first docs/module naming), preserve endpoint compatibility as needed.

---

## 9) Extract path role (legacy/backfill vs canonical mainline)
Status: **Partial**

Evidence:
- root `extract-beads.py` remains substantial and operational.
- internalized pipeline exists behind wrappers.

Gap:
- extract path not yet clearly constrained to legacy/backfill/replay in architecture posture.

Mapped phase/ticket:
- **V2-P5-T2**: explicit deprecation posture + canonical mainline enforcement.

---

## 10) Flush contract embodiment vs docs
Status: **Partial**

Evidence:
- docs are strong (`v2_flush_transaction_spec.md`).
- flush checkpoints now exist (`flush-checkpoints.jsonl` via orchestrator).

Gap:
- full stage-level failure injection + deterministic resume semantics need enforcement.

Mapped phase/ticket:
- **V2-P3-T5**: flush stage fault-injection tests and resume behavior hardening.

---

## Priority order for next implementation wave
1. **V2-P3-T1/T2/T3/T5** (runtime center + session authority + strict flush safety)
2. **V2-P4-T1/T3/T4/T5** (rolling/association/schema closure)
3. **V2-P5-T1/T2** (integration framing + legacy path cleanup)

---

## Success definition for this checklist
Checklist is complete when each item is upgraded to **Realized** or explicitly archived as intentional non-goal.
