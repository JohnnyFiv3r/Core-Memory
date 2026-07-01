# PRD: Ragie Retrieval Fan-out Removal (vendor sunset)

**Status:** Spec — implementation pending
**Effort:** ~0.5 day (delete live path) + ~0.5 day (reference audit + regression)
**Reverses:** `multi-store-recall-fanout.md` (Ragie portion only — PipeHouse retained)
**Trigger:** the Ragie API sunsets **2026-07-19**; the optional Ragie evidence fan-out in
`recall()` stops working that day.

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

---

## Architectural invariant (unchanged)

Core Memory is the causal anchor; external stores are optional evidence sources that never
own causal edges (`multi-store-recall-fanout.md`). Removing Ragie leaves that invariant
intact — **PipeHouse stays under the same contract.** Core Memory must remain tolerant of
*already-persisted* beads/evidence carrying a legacy `ragie` store tag: the read path
degrades to the raw-source reference. **This PRD removes the live Ragie path, not the
ability to read historical data.**

---

## Current state (engine footprint on master)

**Live retrieval path — delete:**

| Location | What |
|---|---|
| `retrieval/adapters/ragie_adapter.py` | `retrieve()` → POST `/retrievals` |
| `retrieval/fanout.py:33,37` | `ragie` in default weights + keys |
| `retrieval/fanout.py:91,105-107` | `ragie_cfg` param; `fanout_stores.append("ragie")`; `tasks["ragie"]` |
| `retrieval/fanout.py:119-120,139` | `_call_ragie` (imports the adapter); `store_fn` entry |
| `retrieval/fanout.py:168` | merge loop `("ragie","pipehouse")` |
| `retrieval/fanout.py:3,59` | docstrings |
| `config/feature_flags.py:150-152` | `external_ragie_api_key()` → `CORE_MEMORY_RAGIE_API_KEY` |
| `config/feature_flags.py:161` | fan-out weight docstring (`core_memory,ragie,pipehouse`) |

**Broader reference footprint to audit** (16 files total incl. the above) — `ragie` also
appears as a recognized store-type / evidence origin / persisted-field name in:
`runtime/ingest/external_evidence.py`, `runtime/ingest/source_envelope.py`,
`runtime/associations/coverage.py`, `schema/models.py`, `schema/bead_projection.py`,
`retrieval/causal_recall.py`, `retrieval/agent.py`, `retrieval/lexical.py`,
`persistence/store_add_bead_ops.py`, `persistence/store_management_ops.py`,
`persistence/store_validation_helpers.py`, `soul/summary.py`,
`integrations/mcp/core-memory-agent-guide.md`.

---

## Design

1. **Delete the live Ragie retrieval path.** Remove `ragie_adapter.py`; strip the `ragie`
   branch from `fanout.py` (weights, keys, `ragie_cfg`, append, task, `_call_ragie`,
   `store_fn`, merge loop → `("pipehouse",)`); remove `external_ragie_api_key()` and the
   `ragie` fan-out weight; drop `CORE_MEMORY_RAGIE_API_KEY`.
2. **Update `fanout_recall` callers** to stop passing `ragie_cfg`; confirm `recall()`
   degrades to the `core_memory` primary (+ optional PipeHouse) by construction.
3. **Audit the broader footprint.** Classify each remaining `ragie` reference as either
   **(a) live path → remove**, or **(b) legacy store-type/field tolerance → retain** so
   already-persisted beads/evidence remain readable. Invariant after the sweep: **no new
   write emits a `ragie` store-type; historical reads never error.**
4. **Docs.** Note in `multi-store-recall-fanout.md` that the Ragie source was retired
   (vendor sunset); PipeHouse remains.

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

- No module `core_memory.retrieval.adapters.ragie_adapter`; `pipehouse_adapter.py` intact.
- `fanout.py` has no `ragie` symbols; `recall()` returns `core_memory` (+ optional PipeHouse)
  results with **no dead-endpoint call**.
- `external_ragie_api_key` gone; no `CORE_MEMORY_RAGIE_API_KEY` read anywhere.
- Regression test: recall degrades gracefully with the Ragie source removed — no exception,
  primary results returned.
- Reference audit complete: no *live* path calls Ragie; a historical bead carrying a legacy
  `ragie` tag still hydrates (degrades to raw-source ref).
- Full suite green, incl. `tests/test_public_generic_naming`.

---

## Rollout

Deletion-only; land before **2026-07-19**. No engine migration required — the fan-out is
optional and inert unless `CORE_MEMORY_RAGIE_API_KEY` is set. The companion surface migration
handles ingestion replacement + persisted-reference re-stamp.

---

## Open questions

1. Keep a generic `ExternalRetrievalSource` seam (PipeHouse-shaped) so a future source is a
   config add, or inline PipeHouse now that Ragie is gone? (Recommend: keep the seam.)
2. Any engine-side hydration handler that special-cases a `ragie` store type — remove, or
   keep as tolerated legacy read? (Recommend: keep read-tolerance, remove write-emission.)
