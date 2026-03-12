# Core Memory Line-by-Line Technical Snapshot

Date: 2026-03-11 (UTC)
Scope: Full repository code-reading pass with canonical-path focus
Author: OpenClaw assistant (pairing session)

## Purpose
This document is the long-form technical appendix snapshot ("B") requested during architecture convergence.
It is intended as a stable reference for pruning to canonical event-driven paths.

---

## 1) Canonical runtime write path (confirmed)

### 1.1 Ingress + emission authority
- `core_memory/integrations/api.py`
  - Stable external ingress: `emit_turn_finalized(...)`
  - Resolves root and forwards to canonical ingress
- `core_memory/event_ingress.py`
  - `should_emit_memory_event(trace_depth, origin)` guard
  - `maybe_emit_finalize_memory_event(...)`
    - canonical envelope normalization
    - full-text privacy mode (`assistant_final_ref` + hash) when configured
    - idempotency checks using pass state

### 1.2 Pass-state/idempotency authority
- `core_memory/event_state.py`
  - Turn envelope contract: `TurnEnvelope`
  - Event contract: `MemoryEvent`
  - State file: `.beads/events/memory-pass-state.json`
  - Status log: `.beads/events/memory-pass-status.jsonl`
  - Event stream: `.beads/events/memory-events.jsonl`
  - Core functions:
    - `mark_memory_pass(...)`
    - `try_claim_memory_pass(...)`
    - `get_memory_pass(...)`
    - `emit_memory_event(...)`

### 1.3 Runtime orchestration owner
- `core_memory/memory_engine.py`
  - canonical sequencing owner
  - `process_turn_finalized(...)`
    - normalize request
    - emit finalized-turn event
    - claim pass
    - run worker
    - invoke crawler handoff/update path
  - `process_flush(...)`
    - enrichment barrier
    - merge crawler side updates
    - run consolidate pipeline

### 1.4 Worker role (currently mechanical)
- `core_memory/event_worker.py`
  - worker intentionally does not own semantic judgment
  - writes pass completion + metric row
  - returns structural delta shell

### 1.5 Store/persistence surfaces
- `core_memory/store.py`
  - session JSONL is live write authority
  - `index.json` is projection/cache
  - event log is audit/rebuild channel
- `core_memory/events.py`
  - event append helpers
  - deterministic index rebuild from durable surfaces

---

## 2) Canonical runtime retrieval path (confirmed)

### 2.1 Public tool surface
- `core_memory/tools/memory.py`
  - canonical wrappers:
    - `get_search_form`
    - `search`
    - `execute`
    - `reason`

### 2.2 Typed execution path
- `core_memory/memory_skill/execute.py`
  - request normalization
  - typed form snap + typed search
  - confidence/next_action computation
  - optional reasoner fallback for grounding
  - never-empty result contract behavior

### 2.3 Ranking stack
- `core_memory/retrieval/hybrid.py`
  - semantic + lexical normalization and fusion
- `core_memory/retrieval/rerank.py`
  - feature reranking (coverage, structural support, penalties)
- `core_memory/retrieval/quality_gate.py`
  - retry/accept decision thresholds

---

## 3) File-level architecture classification

### 3.1 Keep as canonical path owners (high confidence)
- `core_memory/event_ingress.py`
- `core_memory/event_state.py`
- `core_memory/memory_engine.py`
- `core_memory/event_worker.py` (mechanical scope)
- `core_memory/integrations/api.py`
- `core_memory/tools/memory.py`
- `core_memory/memory_skill/execute.py`
- `core_memory/memory_skill/search.py`
- `core_memory/retrieval/query_norm.py`
- `core_memory/retrieval/hybrid.py`
- `core_memory/retrieval/rerank.py`
- `core_memory/retrieval/quality_gate.py`
- `core_memory/events.py`
- `core_memory/session_surface.py`
- `core_memory/live_session.py`
- `core_memory/rolling_record_store.py`

### 3.2 Transitional but currently useful
- `core_memory/graph.py` (re-export façade)
- `core_memory/graph_structural.py`
- `core_memory/graph_semantic.py`
- `core_memory/graph_traversal.py`
- `core_memory/semantic_index.py`
- `core_memory/association/crawler_contract.py`

### 3.3 Explicitly legacy/deprecated compatibility surfaces
- `core_memory/trigger_orchestrator.py` (compat shim)
- `core_memory/openclaw_integration.py` (deprecated wrapper)
- `core_memory/rolling_surface.py` (derived renderer)
- `core_memory/write_triggers.py` (deprecated trigger module)
- `core_memory/association/pass_engine.py` (legacy helper)

---

## 4) High-risk complexity hotspots

### 4.1 `core_memory/store.py` (primary hotspot)
Observed characteristics:
- very large mixed-responsibility class
- persistence + promotion policy + metrics + migration + retrieval helpers
- compatibility delegators + active business logic coexist

Risk:
- change blast radius is high
- onboarding complexity is high
- subtle authority-path regressions likely if edits are not narrowly scoped

### 4.2 Refactor artifact smell in `store.py`
- `_sanitize_bead_content` appears in both delegator and local-implementation patterns in the same file region.
- This should be reconciled to a single authoritative implementation path to reduce confusion and accidental dead logic.

### 4.3 Projection/authority ambiguity risk
- Docs are clear that session/event surfaces are authority and `index.json` is projection.
- Some convenience paths still make projection-first usage easy for contributors.
- Guardrails/docs/tests should continue enforcing authority invariants.

---

## 5) Canonical invariants to preserve during pruning

1. **One turn → one pass key** (`session_id:turn_id`) with idempotent claim semantics.
2. **Write authority is event/session-first**, not transcript/index dump.
3. **`index.json` is rebuildable projection/cache**, never treated as sole source of truth.
4. **Worker stays mechanical unless authority model is explicitly changed.**
5. **Typed retrieval surfaces remain stable** (`tools/memory.py` wrappers).
6. **Fallback behavior must remain deterministic** (sorting/tie-break policy retained).
7. **Deprecated shims must not become hidden primary paths.**

---

## 6) Suggested pruning sequence (safe ordering)

1. Fence deprecated modules with runtime warnings + tests confirming non-primary usage.
2. Extract `MemoryStore` policy blocks into dedicated modules (promotion/metrics/migration).
3. Collapse duplicate/dead helper paths in `store.py` (especially hygiene delegator overlap).
4. Tighten docs + tests around authority hierarchy (event/session > projection).
5. Remove legacy shims only after integration contract tests pass without them.

---

## 7) Test posture snapshot

- Test suite footprint is broad (`tests/` includes architecture, retrieval, authority, integration, sidecar, schema, and parity tests).
- Local runtime in this session could not run pytest due to missing binary in environment.
- Python compile sanity check succeeded (`compileall`).

---

## 8) Snapshot status

This file is the durable reference snapshot requested for the pairing phase.
Use this as the baseline when making deprecation/pruning decisions and when validating that canonical event-driven paths remain intact.
