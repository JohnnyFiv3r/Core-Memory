# PRD: Ragie Removal — retrieval fan-out + `ragie_document_id` schema deprecation

**Status:** Spec — implementation pending
**Effort:** ~0.5 day (delete fan-out) + ~1 day (schema-field deprecation + audit + regression)
**Reverses:** `multi-store-recall-fanout.md` (Ragie portion only — PipeHouse retained)
**Trigger:** the Ragie API sunsets **2026-07-19**; the optional Ragie evidence fan-out in
`recall()` stops working that day. Ragie also left a vendor-named field in the bead schema.

---

## Problem

`recall()` can fan out to an optional Ragie evidence source (see
`multi-store-recall-fanout.md`). Ragie is shutting down — service ends **2026-07-19**. After
that date the fan-out adapter, its feature flag, and the `ragie` store weight are dead code
that will **time out or raise against a dead endpoint** whenever
`CORE_MEMORY_RAGIE_API_KEY` is set. The Ragie retrieval path must be removed cleanly, and
recall must degrade to the `core_memory` primary.

Embedding, indexing, and recall already live in Core Memory's hybrid pipeline
(`pipeline/canonical.py`); the Ragie fan-out was always an *optional* evidence source, never
the primary. Its removal is a graceful degrade, not a capability loss.

Separately, Ragie left a **vendor-named field `ragie_document_id` in the bead schema**
(`schema/models.py`), threaded through ~10 files — a privileged vendor reference that bends
the "no vendor privilege" invariant. This PRD also deprecates it toward the generic
`document_id` / `raw_source_object_id` that already sit beside it.

---

## Architectural invariant (unchanged)

Core Memory is the causal anchor; external stores are optional evidence sources that never
own causal edges (`multi-store-recall-fanout.md`). Removing Ragie leaves that invariant
intact — **PipeHouse stays under the same contract.** Core Memory must remain tolerant of
*already-persisted* beads/evidence carrying a legacy `ragie` store tag: the read path
degrades to the raw-source reference. **This PRD removes the live Ragie path, not the
ability to read historical data.**

---

## Current state — two Ragie footprints in the engine

### Footprint 1 — the optional retrieval fan-out (delete)

| Location | What |
|---|---|
| `retrieval/adapters/ragie_adapter.py` | `retrieve()` → POST `/retrievals` |
| `retrieval/fanout.py:33,37` | `ragie` in default weights + keys |
| `retrieval/fanout.py:91,105-107` | `ragie_cfg` param; `fanout_stores.append("ragie")`; `tasks["ragie"]` |
| `retrieval/fanout.py:119-120,139` | `_call_ragie` (imports the adapter); `store_fn` entry |
| `retrieval/fanout.py:168` | merge loop `("ragie","pipehouse")` |
| `retrieval/fanout.py:3,59` | docstrings |
| **`retrieval/agent.py:937-947`** | **live caller** — reads `external_ragie_api_key()`, passes `ragie_cfg=` into `fanout_recall` |
| `config/feature_flags.py:150-152` | `external_ragie_api_key()` → `CORE_MEMORY_RAGIE_API_KEY` |
| `config/feature_flags.py:161` | fan-out weight docstring (`core_memory,ragie,pipehouse`) |

### Footprint 2 — `ragie_document_id`, a vendor field in the bead schema (deprecate)

A **vendor name is privileged in the engine's public bead schema** — `schema/models.py:769`,
in the document/media field family beside the vendor-neutral `document_id` /
`raw_source_object_id`:

```
document_id: Optional[str] = None
raw_source_object_id: Optional[str] = None
ragie_document_id: Optional[str] = None   # <- vendor-named field
```

Threaded through ~10 files — projection, persistence (incl. validation that accepts
`document_id OR ragie_document_id`), retrieval field lists, and ingest that *requires* one:

| Location | What `ragie_document_id` is |
|---|---|
| `schema/models.py:769` | field on the bead model |
| `schema/bead_projection.py:56` | projected field |
| `persistence/store_add_bead_ops.py:88`, `store_management_ops.py:18,57` | persisted field |
| `persistence/store_validation_helpers.py:111` | validation: `document_id or ragie_document_id` required |
| `retrieval/lexical.py:61`, `retrieval/causal_recall.py:107` | field in retrieval/hydration lists |
| `runtime/ingest/source_envelope.py:59,199`, `runtime/ingest/external_evidence.py:210,248,258,275,342-343` | ingest maps it; `external_evidence` *requires* `document_id or ragie_document_id` |
| `runtime/associations/coverage.py:682,1314` | read in coverage |
| `soul/summary.py:210` | referenced |
| `integrations/mcp/core-memory-agent-guide.md:120` | documented field |

This bends the **"all frameworks are equal adapters; no vendor gets privileged access"**
invariant. The generic `document_id` / `raw_source_object_id` already sit beside it and the
engine already falls back to them — so the field is deprecable, not load-bearing.

---

## Design

### A. Delete the retrieval fan-out (footprint 1)
1. Remove `ragie_adapter.py`; strip the `ragie` branch from `fanout.py` (weights, keys,
   `ragie_cfg`, append, task, `_call_ragie`, `store_fn`, merge loop → `("pipehouse",)`);
   remove `external_ragie_api_key()` and the `ragie` fan-out weight; drop
   `CORE_MEMORY_RAGIE_API_KEY`.
2. **Update the caller `retrieval/agent.py:937-947`** — stop reading `external_ragie_api_key()`
   and stop passing `ragie_cfg=` into `fanout_recall`. Confirm `recall()` degrades to the
   `core_memory` primary (+ optional PipeHouse) by construction.

### B. Deprecate the `ragie_document_id` schema field (footprint 2)
Phased, so historical beads stay readable:
1. **Read-tolerance (now).** Everywhere the engine reads `ragie_document_id`, keep the
   `document_id or ragie_document_id` fallback so already-persisted beads still resolve.
2. **Stop new writes.** New beads populate only `document_id` / `raw_source_object_id`; relax
   the `external_evidence` validation (`:342-343`) to require `document_id` /
   `raw_source_object_id` (not `ragie_document_id`). Coordinated with the surface migration,
   which already writes vendor-neutral provenance.
3. **Drop the field (later).** Once no active beads depend on it, remove `ragie_document_id`
   from `schema/models.py`, the projection, persistence, retrieval lists, coverage, and soul —
   restoring the no-vendor-privilege invariant. Backfill any surviving value into `document_id`.

### C. Docs
Note in `multi-store-recall-fanout.md` that the Ragie source was retired; PipeHouse remains.

---

## Non-goals

- **PipeHouse fan-out** — retained under the same contract.
- **The downstream consumer's ingestion-lane replacement and the re-stamp of persisted
  `store:"ragie"` hydration references** — tracked in the companion ingestion-migration spec
  (integration-surface repo), out of engine scope.
- Reintroducing any third-party retrieval/vector store. Core Memory's hybrid index is the
  store.

---

## Acceptance criteria

**Fan-out (footprint 1):**
- No module `core_memory.retrieval.adapters.ragie_adapter`; `pipehouse_adapter.py` intact.
- `fanout.py` has no `ragie` symbols; **`retrieval/agent.py` no longer reads
  `external_ragie_api_key()` or passes `ragie_cfg`**; `recall()` returns `core_memory`
  (+ optional PipeHouse) with **no dead-endpoint call**.
- `external_ragie_api_key` gone; no `CORE_MEMORY_RAGIE_API_KEY` read anywhere.
- Regression test: recall degrades gracefully with the Ragie source removed.

**Schema field (footprint 2):**
- New beads populate only `document_id` / `raw_source_object_id`; **no new bead writes
  `ragie_document_id`**.
- `external_evidence` no longer *requires* `ragie_document_id` (accepts `document_id` /
  `raw_source_object_id`).
- A historical bead carrying `ragie_document_id` still resolves via the read-tolerance fallback.
- (Later phase) `ragie_document_id` removed from the schema; value backfilled into `document_id`.

- Full suite green, incl. `tests/test_public_generic_naming`.

---

## Rollout

**Footprint 1 (fan-out): before 2026-07-19.** Deletion-only; the fan-out is optional and inert
unless `CORE_MEMORY_RAGIE_API_KEY` is set.

**Footprint 2 (schema field): phased, not time-boxed to the sunset.** Read-tolerance + stop new
writes can land immediately (coordinated with the surface migration's vendor-neutral
provenance); dropping the field waits until no active beads depend on it. Historical reads
never break.

---

## Open questions

1. Keep a generic `ExternalRetrievalSource` seam (PipeHouse-shaped) so a future source is a
   config add, or inline PipeHouse now that Ragie is gone? (Recommend: keep the seam.)
2. Any engine-side hydration handler that special-cases a `ragie` store type — remove, or
   keep as tolerated legacy read? (Recommend: keep read-tolerance, remove write-emission.)
