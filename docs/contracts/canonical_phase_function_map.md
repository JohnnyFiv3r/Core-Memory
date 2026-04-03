# Canonical Phase → Function Map

Status: Canonical
Date: 2026-03-15
Audience: Product + Engineering

Purpose: Make the architecture auditable by mapping each conceptual phase to the concrete module/function owner.

---

## 0) System boundary

- **Canonical write orchestration owner:** `core_memory.runtime.engine`
- **Canonical persistence owner:** `core_memory.persistence.store`
- **Canonical retrieval orchestration owner:** `core_memory.retrieval.pipeline`
- **Canonical retrieval tool surface:** `core_memory.retrieval.tools`

If behavior changes, these owners must be updated first.

---

## A) Write-side canonical phases

### A1. Turn ingress / dedupe
**Concept:** Receive finalized turn and enforce one-pass-per-turn.

- `core_memory.runtime.engine.process_turn_finalized(...)`
- `core_memory.runtime.ingress.maybe_emit_finalize_memory_event(...)`
- `core_memory.runtime.state.try_claim_memory_pass(...)`
- `core_memory.runtime.state.get_memory_pass(...)`

**Invariant:** idempotent by `(session_id, turn_id)`.

---

### A2. Turn bead write
**Concept:** Ensure one current-turn bead is written.

- `core_memory.runtime.engine._ensure_turn_creation_update(...)`
- `core_memory.association.crawler_contract.apply_crawler_updates(...)`
- `core_memory.persistence.store.MemoryStore.add_bead(...)`

**Invariant:** each finalized turn has a canonical write candidate.

---

### A3. Association pass
**Concept:** Append relevant in-session associations.

- `core_memory.runtime.association_pass.run_association_pass(...)`  ← runtime invocation owner
- `core_memory.association.crawler_contract.apply_crawler_updates(...)`
- `core_memory.policy.association_contract.*`
  - `normalize_assoc_row`
  - `assoc_row_is_valid`
  - `assoc_dedupe_key`

**Invariant:** normalized + deduped association appends only.

---

### A4. Decision pass (promoted/candidate/null)
**Concept:** Decide promotion state for visible session beads.

- `core_memory.runtime.decision_pass.run_session_decision_pass(...)`  ← runtime invocation owner
- `core_memory.persistence.store.MemoryStore.decide_session_promotion_states(...)`
- `core_memory.policy.promotion_contract.*`
  - `current_promotion_state`
  - `is_promotion_locked`
  - `validate_transition`
  - `classify_signal`

**Invariant:** promotion is terminal (no demotion/unpromotion).

---

### A5. Flush/consolidate (session cycle)
**Concept:** archive → compact → rolling-window maintenance.

- `core_memory.runtime.engine.process_flush(...)`  ← canonical owner
- `core_memory.association.crawler_contract.merge_crawler_updates_for_flush(...)`
- `core_memory.write_pipeline.orchestrate.run_consolidate_pipeline(...)`
- `core_memory.write_pipeline.consolidate.run_session_consolidation(...)`

**Invariant:**
- once-per-cycle guard (`already_flushed_for_latest_turn`)
- phase trace includes:
  - `archive_compact_session`
  - `rolling_window_write`
  - `archive_compact_historical`

---

### A6. Flush artifacts + checkpoints
**Concept:** write operational checkpoints and reports.

- `core_memory.runtime.engine.process_flush(...)`
- `core_memory.persistence.io_utils.append_jsonl(...)`
- Artifact path: `.beads/events/flush-checkpoints.jsonl`
  - `openclaw.memory.flush_checkpoint.v1`
  - `openclaw.memory.flush_report.v1`

---

## B) Retrieval-side canonical phases

### B1. Query normalization + intent handling
- `core_memory.retrieval.query_norm`
- `core_memory.retrieval.config`

### B2. Canonical request normalization
- `core_memory.retrieval.normalize`
- `core_memory.retrieval.query_norm`

### B3. Search execution path
- `core_memory.retrieval.pipeline.canonical.search_request`
- `core_memory.retrieval.semantic_index`
- `core_memory.retrieval.visible_corpus`

### B4. Reasoning / causal grounding path
- `core_memory.retrieval.pipeline.canonical.trace_request`
- `core_memory.graph.semantic`
- `core_memory.graph.structural`
- `core_memory.graph.traversal`
- `core_memory.retrieval.semantic_index`

### B5. Canonical execute orchestration + explanation output
- `core_memory.retrieval.pipeline.canonical.execute_request`
- `core_memory.retrieval.tools.memory.execute(...)`

**Invariant:** retrieval is not session-limited; global historical beads remain searchable.

---

## C) Integration phases

### C1. OpenClaw bridge hooks
- `core_memory.integrations.openclaw_agent_end_bridge`
- `core_memory.integrations.openclaw_compaction_bridge`
- `core_memory.integrations.openclaw_runtime` (compat integration helpers)

### C2. Public integration APIs
- `core_memory.integrations.api`
- `core_memory.integrations.http`
- `core_memory.integrations.pydanticai`
- `core_memory.integrations.springai`

---

## D) Operator command surface

- `core_memory.cli`
  - `consolidate` (canonical flush command)
  - `metrics canonical-health`

---

## E) Deprecated / transitional paths (track for removal)

- `core_memory.integrations.openclaw_runtime` (compat helper surface)

These should never be treated as canonical owners.

---

## F) Quick validation commands

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -q
python3 -m core_memory.cli --root . metrics canonical-health
```

---

## G) Change-control rule

Any PR that changes phase ownership must update this file in the same PR.
ate this file in the same PR.
