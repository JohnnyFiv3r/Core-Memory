# Immutable Causal Edge Contract + Implementation Plan

## Decision Summary (Locked)

1. **Causal/structural links are immutable edges** (append-only).
2. **Semantic/topic edges are mutable** (reinforce/decay/deactivate).
3. Retrieval and grounded reasoning treat **graph structural edges as canonical truth**.
4. `associations` and `bead.links` are upstream/input surfaces; graph edge events are runtime truth.

---

## Edge Taxonomy (Authoritative)

## A) Immutable Causal Edges (class=`structural`, immutable=`true`)

Use for provenance/causal grounding:
- `caused_by`
- `supports`
- `derived_from`
- `supersedes`
- `superseded_by`
- `contradicts`
- `resolves`

Rules:
- Created via `edge_add` events only.
- Never decayed or auto-deactivated.
- Can be corrected only by append-only compensating events (no in-place rewrite).

## B) Mutable Semantic Edges (class=`semantic`, immutable=`false`)

Use for discovery/navigation:
- `related_to`
- `similar_to`
- topic/tag affinity links

Rules:
- Weight updates are append-only events.
- May decay/deactivate.
- Never used as sole evidence for grounded causal explanation.

---

## Pipeline Contract (Required Invariants)

Expected data flow:

1. **Association crawler** writes `index.associations` rows.
2. Sync stage maps associations => normalized causal `bead.links`.
3. Sync stage emits missing immutable `edge_add` events from causal links.
4. Graph build materializes immutable edges into `bead_graph.json`.

Invariant checks:
- Every structural association has a corresponding link.
- Every structural link has a corresponding structural immutable edge event.
- Every structural edge event appears in graph head after build.

Any invariant break should produce a failing report in strict mode.

---

## Immediate Priority: Immutable Causal Beads Fix (Most Critical)

Goal: close grounding failures by ensuring causal links are actually materialized as immutable graph edges.

## Phase 1 — Contract + Mapping (small)

Deliverables:
- `core_memory/data/structural_relation_map.json`
- `docs/structural_pipeline_contract.md` (can reference this doc)

Mapping example:
```json
{
  "caused_by": "caused_by",
  "supports": "supports",
  "derived_from": "derived_from",
  "supersedes": "supersedes",
  "superseded_by": "superseded_by",
  "contradicts": "contradicts",
  "resolves": "resolves"
}
```

## Phase 2 — Deterministic Sync Command

Add:
- `core_memory/sync_structural.py`
- CLI command: `core-memory graph sync-structural [--apply] [--strict]`

Behavior:
- Dry-run by default; report counts + diffs.
- `--apply` performs:
  1) associations -> links hydration
  2) links -> immutable structural edge events
  3) graph materialization
- Deterministic ordering and idempotent writes.

## Phase 3 — Invariant Validator

Add strict checks:
- `missing_link_from_association`
- `missing_edge_from_link`
- `missing_graph_head_from_edge`

Output machine-readable JSON + compact summary.

## Phase 4 — Backfill Existing Corpus

One-time operator run:
1. backup index and events
2. run `sync-structural --apply --strict`
3. rebuild graph
4. run retrieval eval and grounding diagnostics

Success criteria:
- `causal_grounding_rate` increases on KPI set
- per-case details show `has_structural=true` for causal queries where decision/evidence exist

---

## Secondary Plan (After Immutable Fix)

Only after immutable causal backbone is healthy:
- keep semantic edge decay/reinforcement for topic navigation
- use semantic edges for expansion/filtering/clustering
- keep grounded explanations structural-first

---

## Test Plan (Required)

1. `test_sync_association_to_links.py`
2. `test_sync_links_to_immutable_edges.py`
3. `test_sync_end_to_end_invariants.py`
4. `test_sync_idempotent_reapply.py`
5. `test_causal_grounding_improves_on_fixture.py` (deterministic fixture)

---

## Non-Goals (for this fix)

- No deep graph traversal changes.
- No LLM planner/query rewrite additions.
- No new external graph DB.
- No semantic scoring expansion before causal invariant closure.
