# Execution Plan: Search Quality and Enrichment (TODOs #3, #5, #7, #9)

**Author:** Core Memory team  
**Date:** 2026-05-28  
**Branch:** `claude/validate-demo-todos-SCRSz`  
**Status:** Complete — TODOs #3, #5, #7, and #9 shipped

---

## Current implementation note

This execution plan is complete. The shipped surfaces are:

- TODO #3: `apply_crawler_updates` fills missing association relationships with
  the preview classifier before strict validation.
- TODO #5: claim updates dedupe repeated grounding hashes per `(subject, slot)`
  with warning telemetry.
- TODO #7: semantic lifecycle ergonomics include autodrain state, status/doctor
  queue health, and `semantic backfill`.
- TODO #9: session enrichment delta analysis and Slice B implementation shipped,
  including `enrichment_run_id`, idempotency replay, persisted
  `session_enrichment_delta.v1` envelopes, and stage-results coverage.

Representative proof tests:
`tests/test_association_classifier_fallback.py`,
`tests/test_claim_grounding_dedup.py`, `tests/test_semantic_autodrain.py`,
`tests/test_enrichment_slice_b.py`, `tests/test_f_w1_enrichment_queue.py`, and
`tests/test_session_enrichment_delta.py`.

The plan below is retained as the historical execution record.

---

## Scope

This plan addressed four TODO items from `demo/TODO.md` that had remained open
after Phase 9–10 cleanup. Items were prioritized by their impact on search
quality and memory correctness for external contributors.

| TODO | Title | Priority |
|------|-------|----------|
| #3 | Classifier fallback for missing relationships | High — prevents quarantine of valid associations |
| #5 | Grounding-hash dedup for ClaimUpdates | High — prevents duplicate state oscillation |
| #7 | Semantic ergonomics (auto-drain, backfill, metrics) | Medium — operational gap for users |
| #9 | Unified enrichment delta analysis artifact + Slice B implementation | Medium — makes enrichment replay/idempotency inspectable |

---

## TODO #3 — Classifier Fallback in Association Write Path

**Problem:** An agent-authored association with an empty `relationship` field is quarantined by `validate_and_normalize_inference_payload` (strict mode). This is correct behavior for truly unknown relationships, but the preview classifier can infer a canonical relationship from bead content — and the agent may have simply omitted the field, not actively disagreed.

**User decision:** Run classifier only when `relationship` is empty/missing (never override agent-supplied types).

**Implementation:** In `apply_crawler_updates` in `association/crawler_contract.py`, before calling `validate_and_normalize_inference_payload`, check `row.get("relationship")`. If empty, look up the source and target bead dicts from the index and call `_infer_relationship(sb, tb)` from `association/preview.py`. Inject the result into `row` with `provenance = "preview_classifier"`. The original validation then runs on the enriched row.

**No layering violation:** `preview.py` and `crawler_contract.py` are both in the `association/` module.

**Files changed:** `core_memory/association/crawler_contract.py`, `core_memory/association/preview.py` (expose helper)

---

## TODO #5 — Grounding-Hash Dedup for ClaimUpdates

**Problem:** `_append_claim_update_rows` deduplicates by `(target_claim_id, decision, replacement_claim_id, grounding_hash)`. However, the same evidence-based judgment (`grounding_hash`) applied to a different `target_claim_id` for the same `(subject, slot)` pair bypasses this dedup, potentially causing state oscillation in `resolve_current_state()`.

**User decision:** Skip silently on duplicate + emit telemetry event (log at WARNING level with structured fields for observability).

**Implementation:** In `_append_claim_update_rows` in `persistence/store_claim_ops.py`, build a `seen_grounding_per_slot: set[tuple[str, str, str]]` keyed by `(subject, slot, grounding_hash)` from existing updates in the full index. Before appending each incoming row, check if its hash is already present for that slot. If so, log a structured warning and skip.

**Files changed:** `core_memory/persistence/store_claim_ops.py`

---

## TODO #7 — Semantic Ergonomics

**Problem:** Three operational gaps in the semantic lifecycle:
1. Rebuilds require explicit CLI invocation — users forget to drain the queue
2. `semantic status` doesn't show queue depth or autodrain state
3. No `semantic backfill` command for model migration / re-embedding
4. `semantic doctor` doesn't report queue health (stuck queue, stale epoch)

**User decisions:**
- Auto-drain: in-process background thread on first `mark_semantic_dirty()` call
- `CORE_MEMORY_SEMANTIC_AUTODRAIN` env var, default `on`

**Implementation:**
1. `lifecycle.py`: Add module-level `_DRAIN_LOCK: threading.Lock` + `_DRAIN_THREADS: dict[str, Thread]`. In `mark_semantic_dirty`, after enqueuing, check `CORE_MEMORY_SEMANTIC_AUTODRAIN != "off"` and start a daemon thread calling `run_async_jobs` if no live thread exists for this root.
2. `lifecycle.py` → `semantic_status`: Add `autodrain_enabled` field.
3. `cli/__init__.py`: Add `semantic backfill` sub-command (no flags needed).
4. `handlers/semantic.py`: Handle `backfill` (enqueue reconcile + wait), extend `status` with autodrain info, extend `doctor` with queue health.

**Files changed:** `core_memory/retrieval/lifecycle.py`, `core_memory/cli/__init__.py`, `core_memory/cli/handlers/semantic.py`

---

## TODO #9 — Slice A: Enrichment Delta Analysis Artifact

**Deliverables:** `docs/PRD/session-enrichment-delta-analysis.md` and
`docs/PRD/session-enrichment-delta-slice-b.md`

This document maps the current enrichment pipeline (all 9 stages of `run_turn_enrichment`) against the proposed `session_enrichment_delta.v1` envelope, identifying:
- Idempotency boundary for each stage
- Overlapping semantic judgments
- Window surfaces that must be stable across re-runs
- Field inventory for the delta envelope
- Migration risks

See the analysis and Slice B documents for full details.

---

## Execution Order

1. Write this planning document *(done)*
2. Code: TODO #3 classifier fallback *(done)*
3. Code: TODO #5 grounding-hash dedup *(done)*
4. Code: TODO #7 auto-drain + CLI extensions *(done)*
5. Docs/code: TODO #9 analysis artifact + Slice B implementation *(done)*
6. Update `docs/status.md` and `docs/cleanup-plan.md` *(done)*
7. Tests, commit, push *(done)*

---

## Test Coverage Targets

| Change | Test file |
|--------|-----------|
| Classifier fallback | `tests/test_association_classifier_fallback.py` |
| Grounding-hash dedup | `tests/test_claim_grounding_dedup.py` |
| Semantic auto-drain | `tests/test_semantic_autodrain.py` |
| Semantic backfill CLI | Covered in semantic handler test |
| Enrichment delta Slice B | `tests/test_enrichment_slice_b.py`, `tests/test_f_w1_enrichment_queue.py`, `tests/test_session_enrichment_delta.py` |
