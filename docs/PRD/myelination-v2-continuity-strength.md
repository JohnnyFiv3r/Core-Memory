# PRD: Myelination v2 — Unified Continuity Strength

> **⚠️ SUPERSEDED.** Slice A (unified edge strength) folds into
> `myelination-reinforcement.md`; Slice B (per-bead continuity-depth scalar)
> migrates to Dreamer V3 **Assembly Depth**; Slice C (geometry/projection export)
> migrates to Dreamer V3 §16.1 (deferred). See `myelination-reinforcement.md` §0.
> Kept for history.

Status: Proposed
Depends on: edge lifecycle (shipped), worldline derivation (shipped),
myelination experiment MYE-1 (shipped, flag-gated)

---

## Problem

Core Memory now has **three parallel "strength" systems** that each capture a
slice of how embedded a memory is in the continuity structure:

1. **Myelination (MYE-1)** — edge-first *quality* signal: success/failure
   participation from `retrieval-feedback.jsonl`, projected onto endpoint
   beads. Flag-gated (`CORE_MEMORY_MYELINATION_ENABLED`), default off.
2. **Edge lifecycle** — edge *quantity + recency* signal:
   `reinforcement_count` / `last_reinforced_at` folded at flush from
   `edge-usage.jsonl`, with bounded bonus, half-life decay, and supersession
   penalty. Always on.
3. **Retrieval value bonuses** — per-bead overrides and index-resident
   `retrieval_value_bonus` consumed during ranking.

Fragmentation costs:
- Two edge-strength multipliers with different stores, cadences, and flags —
  scoring semantics are split and hard to reason about.
- No single **per-bead continuity-depth scalar** exists, although every input
  for one now does. The continuity-geometry visualization layer (radial axis), the
  external self-model, and the external predictive layer all need exactly this
  projection — by the agreed boundary, those consumers live *outside* the
  graph and consume Core Memory projections.

## Goals

1. **One edge-strength model.** `strength(edge) = quality × usage × recency ×
   status`, where quality comes from myelination's success/fail signal and
   usage/recency/status from the edge lifecycle. Single multiplier function in
   `graph/edge_weights.py`; single documented range.
2. **`continuity_depth(bead)` manifest.** Flush-time computation of a bounded
   [0, 1] per-bead scalar from: lifecycle-weighted active edge degree,
   myelination bead bonus, retrieval value, worldline membership
   (`worldline_membership()`), promotion status, and goal linkage. Persisted
   to `.beads/continuity-manifest.json` (myelination-manifest pattern).
3. **Projection surfaces.** `core_memory.continuity_depth(root)` +
   `GET /v1/memory/projection/continuity`; bulk geometry export
   (`GET /v1/memory/projection/geometry`) returning beads (id, created_at,
   type, status, depth) + edges (src, dst, rel, strength, provenance,
   lifecycle fields) in one call.
4. **Default-on.** Retire the MYE-1 experiment flag; quality signal becomes a
   permanent input with neutral behavior when feedback volume is low.

## Non-goals

- Identity/self-model synthesis (external consumer, by boundary decision).
- Probabilistic forecasting (external predictive layer).
- Using `continuity_depth` as a retrieval ranking term (future experiment;
  this PRD ships it as a projection only so consumers don't fork formulas).
- Deleting or down-weighting memories below a depth threshold (decay demotes,
  never deletes).

## Design

### Slice A — unify edge strength
- Extend `effective_edge_multiplier(assoc)` to accept an optional
  myelination edge-quality term (keyed by the existing
  `bonus_by_edge_key`), replacing the separate bonus application in hop
  expansion and `root_cause_trace`.
- One clamped output range, documented in `docs/edge_lifecycle.md`.

### Slice B — continuity manifest
- New `core_memory/graph/continuity.py`: `compute_continuity_depth(root)`
  → `{bead_id: depth}` with per-input breakdown for explainability.
- Normalization: each input z-scaled/percentile-ranked within the store, then
  weighted-summed (weights env-tunable, defaults documented) and squashed to
  [0, 1]. Deterministic for a fixed store state.
- Fold at flush after `fold_edge_usage`; stats on flush result as
  `continuity_manifest`.

### Slice C — projection surfaces
- Public API export + HTTP routes (generic projection paths, not
  deployment-privileged — adapter law).
- Geometry export includes manifest version + computed_at so renderers can
  detect staleness.

### Slice D — flag retirement
- `CORE_MEMORY_MYELINATION_ENABLED` removed; doctor migration note; contract
  doc `contracts/myelination_experiment_contract.md` superseded by this PRD's
  shipped docs (delete, per docs policy).

## Acceptance criteria

- Single edge-strength function; hop expansion, trace, and backend chain
  scoring all consume it (no forked formulas — the kuzu staleness lesson).
- `continuity_depth` deterministic, bounded, explainable (breakdown per bead).
- Geometry export serves a 10k-bead store in < 1s from manifest (no recompute
  on read).
- Full suite green; causal benchmark (`benchmarks/causal/`) unchanged or
  improved on grounding_full and distractor survival.

## Test plan

- Unit: strength composition (quality×usage×recency×status), depth
  normalization bounds, breakdown explainability, empty-store behavior.
- Integration: flush writes manifest; projection routes serve manifest;
  depth shifts correctly after reinforcement + supersession events.
- Regression: benchmark suite before/after; retrieval ranking unchanged
  (projection-only guarantee).
