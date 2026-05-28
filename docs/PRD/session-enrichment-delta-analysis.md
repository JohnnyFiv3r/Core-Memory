# Session Enrichment Delta — Analysis Artifact (TODO #9 Slice A)

**Author:** Core Memory team  
**Date:** 2026-05-28  
**Status:** Slice A complete — Slice B (implementation) pending

---

## Overview

`run_turn_enrichment` in `runtime/passes/enrichment.py` is a 9-stage synchronous pipeline that fires from the `turn-enrichment` side-effect job kind. Each stage reads from and writes to the same persisted store, making idempotency and delta isolation non-trivial.

This document maps the current pipeline, identifies stage boundaries, overlap risks, and proposes the `session_enrichment_delta.v1` envelope for Slice B.

---

## Call Graph from `process_turn_finalized`

```
emit_turn_finalized
  → process_turn_finalized                    (runtime/engine.py)
      → write bead                            (store.add_bead)
      → crawl associations                    (apply_crawler_updates)
      → emit_claim_updates                    (claim/update_policy.py)
      → promote bead (if eligible)
      → enqueue side-effect: turn-enrichment  (side_effect_queue)

[background worker]:
  drain_side_effect_queue
    → process_side_effect_event("turn-enrichment", ...)
        → run_turn_enrichment(root, payload)   (runtime/passes/enrichment.py)
```

`run_turn_enrichment` is decoupled from `process_turn_finalized` via the queue — it runs asynchronously in a worker drain pass, not inline. This means the bead is already written before enrichment begins.

---

## Stage-by-Stage Analysis

### Stage 1 — Association Pass (preview)

**Code:** `enrichment.py:113-133`  
**Reads:** session surface (last N beads), current bead  
**Writes:** queued side-log event (`association_append`)  
**Idempotency boundary:** `assoc_dedupe_key(src, tgt, rel)` — same (src, tgt, rel) triple is deduped on merge  
**Re-run safe?** Yes — existing dedup prevents double-writing  
**Overlap risk:** The crawler contract's `apply_crawler_updates` also queues associations from agent-issued decisions. Stage 1 adds preview-classifier candidates on top. Both use the same side-log and same dedup key — no collision.

---

### Stage 2 — Claim Extraction

**Code:** `enrichment.py:135-158`  
**Reads:** bead content (title, summary, detail)  
**Writes:** claims embedded in bead (`write_claims_to_bead`)  
**Idempotency boundary:** `_claim_dedupe_key` — deduped by (subject, slot, value, source_bead_id)  
**Re-run safe?** Yes — exact duplicate claims are silently skipped  
**Overlap risk:** Low. Claims extracted from this bead are unique to the bead's content.

---

### Stage 3 — Preview Associations (cross-session)

**Code:** `enrichment.py:160-183`  
**Reads:** full index (up to max_lookback prior beads)  
**Writes:** queued side-log events (same path as Stage 1)  
**Idempotency boundary:** Same as Stage 1  
**Re-run safe?** Yes  
**Overlap risk:** Stage 1 and Stage 3 both write to the same side-log with the same dedup. Separate iterations; the union is correct.

---

### Stage 4 — Crawler Merge

**Code:** `enrichment.py:185-197`  
**Reads:** side-log for session  
**Writes:** merges queued events into `index.json` (associations list)  
**Idempotency boundary:** `(src, tgt, rel)` dedup in `merge_crawler_updates`  
**Re-run safe?** Yes — merge is idempotent for duplicate triples  
**Window surface:** Reads and clears the side-log. If the worker crashes mid-stage, the log may be partially cleared. **This is the primary non-idempotent stage.** A re-run after partial failure may miss associations that were logged but not yet merged.

---

### Stage 5 — Decision Pass

**Code:** `enrichment.py:199-220`  
**Reads:** explicit `claim_updates` from the enrichment payload  
**Writes:** `write_claim_updates_to_bead` via `emit_claim_updates`  
**Idempotency boundary:** `_claim_update_dedupe_key` (target + grounding_hash) + new grounding-hash per-slot dedup (TODO #5)  
**Re-run safe?** Yes (with TODO #5 fix)  
**Overlap risk:** The turn-flow path also calls `emit_claim_updates` synchronously. If the enrichment payload replays the same decisions, grounding-hash dedup prevents double-writing.

---

### Stage 6 — Claim Updates (auto-reconciliation)

**Code:** `enrichment.py:222-240`  
**Reads:** `resolve_current_state(subject, slot)` for each new claim  
**Writes:** `write_claim_updates_to_bead` (supersede/reaffirm)  
**Idempotency boundary:** `_update_dedupe_key`  
**Re-run safe?** Yes  
**Overlap risk:** Same as Stage 5. If both Stage 5 and Stage 6 produce a supersede for the same (target, replacement), the dedup catches it.

---

### Stage 7 — Memory Outcome

**Code:** `enrichment.py:242-262`  
**Reads:** current bead  
**Writes:** `write_memory_outcome_to_bead` (sets `memory_outcome` field on bead row)  
**Idempotency boundary:** Last-write-wins (overwrites in place)  
**Re-run safe?** Yes — repeated writes produce the same outcome  
**Overlap risk:** None.

---

### Stage 8 — Goal Lifecycle

**Code:** `enrichment.py:264-280`  
**Reads:** goal beads (type=goal, status=open) from index  
**Writes:** status transitions (open→in_progress, in_progress→complete) on goal beads  
**Idempotency boundary:** Status can only transition forward; already-transitioned goals are skipped  
**Re-run safe?** Partially. If a goal is marked `complete` on first run and `complete` again on re-run, no change. But if the stage crashes mid-transition (partial write), a re-run may re-evaluate and produce a different decision.

---

### Stage 9 — Quality Metric

**Code:** `enrichment.py:282-295`  
**Reads:** bead content signals  
**Writes:** `quality_score` field on bead  
**Idempotency boundary:** Last-write-wins  
**Re-run safe?** Yes

---

## Overlapping Semantic Judgments

| Pair | What overlaps | Risk |
|------|---------------|------|
| Stage 1 + Stage 3 | Both write preview associations for same session | Low — deduped by (src, tgt, rel) |
| Stage 2 + Stage 6 | Claim extraction (stage 2) feeds auto-reconciliation (stage 6) | Low — stage 6 reads finalized claims written by stage 2 |
| Stage 5 + Stage 6 | Both call `emit_claim_updates` | Medium — can produce redundant supersede rows without grounding-hash dedup; **fixed by TODO #5** |
| Turn-flow `emit_claim_updates` + Stage 5/6 | Turn-flow runs synchronously; enrichment runs async | Low — grounding-hash dedup prevents double-write |

---

## Window Surfaces

| Stage | Window read | Stability requirement |
|-------|-------------|----------------------|
| Stage 1 | Last N session beads (rolling window) | Must be stable during stage; no new beads should be written between read and queue |
| Stage 3 | All beads up to max_lookback | Same |
| Stage 4 | Session side-log | **Must be transactional** — clear and apply must succeed atomically (currently not; see risk above) |
| Stage 6 | `resolve_current_state` snapshot | Must see all claims written by stage 2 in the same run |

---

## Proposed `session_enrichment_delta.v1` Field Inventory

```json
{
  "schema": "session_enrichment_delta.v1",
  "bead_id": "<canonical turn bead>",
  "session_id": "<session>",
  "turn_id": "<turn>",
  "enrichment_run_id": "<uuid>",
  "triggered_at": "<iso8601>",
  "stages_run": ["association_pass", "claim_extraction", "preview_associations",
                  "crawler_merge", "decision_pass", "claim_updates",
                  "memory_outcome", "goal_lifecycle", "quality_metric"],
  "stage_results": {
    "association_pass": {"queued": 0, "skipped_existing": 0},
    "claim_extraction": {"extracted": 0, "skipped_duplicate": 0},
    "preview_associations": {"queued": 0, "skipped_existing": 0},
    "crawler_merge": {"merged": 0, "quarantined": 0},
    "decision_pass": {"emitted": 0, "skipped_grounding": 0},
    "claim_updates": {"emitted": 0, "skipped_grounding": 0},
    "memory_outcome": {"written": false},
    "goal_lifecycle": {"transitioned": 0},
    "quality_metric": {"score": null}
  },
  "idempotency_token": "<grounding_hash of (bead_id, enrichment_run_id)>"
}
```

**Key design decisions for Slice B:**
1. `enrichment_run_id` scopes idempotency — same token = skip all stages
2. Stage 4 (crawler merge) must be wrapped in a `store_lock` that atomically clears the log and writes the index projection; no partial application
3. `stages_run` is a selector — callers can request a subset for re-runs that only need specific stages
4. The delta envelope is stored alongside the bead in `.beads/events/enrichment-<bead_id>.jsonl` for audit

---

## Migration Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Stage 4 partial-clear bug | High | Wrap merge + log-clear in same `store_lock` block |
| Stage 5+6 duplicate supersede without TODO #5 | Medium | TODO #5 already addresses this |
| Re-run divergence on goal lifecycle (Stage 8) | Low | Store `goal_lifecycle_run_ids` set per goal; skip if already processed by this `enrichment_run_id` |
| `run_turn_enrichment` re-entrant calls (two workers for same bead) | Medium | Add per-bead advisory lease in side-effect queue (lease_token already exists) |

---

## Next Step (Slice B)

Implement the `session_enrichment_delta.v1` envelope:
1. Thread `enrichment_run_id` through all 9 stages
2. Wrap Stage 4 merge in atomic lock
3. Persist the delta envelope after stage 9 completes
4. Add idempotency check at the top of `run_turn_enrichment`: if same `enrichment_run_id` in events log, return cached result

**Target file:** `core_memory/runtime/passes/enrichment.py`  
**PRD:** Create `docs/PRD/session-enrichment-delta-slice-b.md` when ready to implement
