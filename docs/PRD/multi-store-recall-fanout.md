# PRD: Multi-Store Recall Fan-out (External Memory Runtime)

**Status:** Implemented — PipeHouse retained; Ragie retired by `ragie-fanout-removal.md`
**Effort:** ~1 day spec review + ~4 days implementation  
**Depends on:** #16 (external data bead ingest contract) for PipeHouse adapter  

> 2026-07-08 update: the original Ragie source described in this PRD has been
> retired before the 2026-07-19 API sunset. Current fan-out is Core Memory plus
> optional PipeHouse. Historical `ragie` source metadata remains readable, but no
> live Ragie retrieval path remains.

---

## Problem

`recall()` originally fanned out to Core Memory's internal semantic index only. Relational
data insights in PipeHouse were invisible to the retrieval pipeline, so an agent query
could return causal memory from conversations but miss a supporting data anomaly.

The result: agents answer from partial context. A question about a vendor decision
returns the decision bead but not the COGS anomaly that prompted it, and not the
contract document that sealed it.

---

## User value

- One query surfaces causal memory and relational data in a single ranked result set.
- Source provenance is preserved end-to-end: every evidence item carries its origin
  store and store-native ID, traceable back to the original file or DB record.
- A store being unavailable degrades gracefully — Core Memory recall proceeds and the
  caller sees which stores were unreachable.

---

## Architectural invariant

**Core Memory is the causal anchor. PipeHouse is an optional evidence source.**

PipeHouse data beads surface as `EvidenceItem` objects whose `source_store` is tagged
accordingly. They do not own causal edges (`led_to`, `caused_by`, etc.) — those edges
exist only between Core Memory beads. The agent does not treat a PipeHouse item as a
first-class node in the causal graph.

This invariant must be enforced at the result-combination layer, not by convention.
`EvidenceItem` objects from non-Core-Memory stores must not carry a `bead_id` that
points into the Core Memory bead store unless a real bead exists for them (e.g. a
transcript bead that shares a unifying ID with an external item — see Unifying ID below).

---

## Current state

| Component | Status |
|-----------|--------|
| `recall()` in `retrieval/agent.py` | Done — Core Memory only |
| Ragie client wrapper | **Retired** |
| PipeHouse read adapter | Done |
| `EvidenceItem.source_store` field | Done |
| `RecallResult.metadata["unavailable_stores"]` | Done |
| Fan-out orchestration in `retrieval/agent.py` | Done — PipeHouse only |
| Score normalization across stores | Done |
| Unifying ID resolution at combination layer | Done |

---

## Success criteria

1. `recall("why did COGS increase last quarter")` returns evidence items from Core
   Memory and PipeHouse in a single ranked `RecallResult.evidence` list.
2. Each `EvidenceItem` carries `source_store` ("core_memory" | "pipehouse") and
   `source_ref` (the store-native ID — PipeHouse `record_id` or Core Memory `bead_id`).
3. When PipeHouse is unreachable, Core Memory results still return and
   `RecallResult.metadata["unavailable_stores"]` lists `["pipehouse"]`.
4. A Core Memory bead and a corresponding external item that share a unifying ID are
   surfaced as a single grouped result, not two separate items.
5. Score normalization is applied before merging: all three stores' scores are
   independently rescaled to [0.0, 1.0] before the combined list is ranked.
6. Fan-out is only activated when at least one external adapter is configured
   (`CORE_MEMORY_PIPEHOUSE_URL` env var is set). Without configuration, `recall()`
   behaves identically to today.

---

## Scope

**In:**
- `retrieval/fanout.py` — fan-out orchestration: parallel calls, result combination,
  score normalization, unifying ID resolution, degraded-mode handling
- `retrieval/adapters/pipehouse_adapter.py` — PipeHouse read call → `list[EvidenceItem]`
- `EvidenceItem` schema additions: `source_store`, `source_ref`
- `RecallResult.metadata["unavailable_stores"]` population
- `recall()` in `retrieval/agent.py` — conditional fan-out when adapters are configured
- Unifying ID grouping at combination layer

**Out:**
- Changes to Core Memory's internal FAISS/Qdrant/pgvector index
- Causal edge creation for PipeHouse items
- Multi-hop causal traversal across store boundaries
- PipeHouse as a write destination (read-only adapter only in this slice)

---

## Result envelope additions

### `EvidenceItem` additions (`retrieval/contracts.py`)

```python
source_store: str = "core_memory"   # "core_memory" | "pipehouse"
source_ref: str = ""                # store-native ID (chunk_id, record_id, or bead_id)
unifying_id: str | None = None      # cross-store join key when present
```

### `RecallResult.metadata` additions

```python
metadata["unavailable_stores"] = []  # list of store names that failed during fan-out
metadata["fanout_stores"] = []       # list of stores that were queried
```

## PipeHouse adapter (`retrieval/adapters/pipehouse_adapter.py`)

PipeHouse exposes a read endpoint (URL configured via `CORE_MEMORY_PIPEHOUSE_URL`) that
accepts a semantic query and returns matched data insight records. The adapter maps
each record to an `EvidenceItem`:

```
record_id       → source_ref
content         → content_excerpt
relevance_score → score (normalize before merging)
entity_refs     → metadata["entity_refs"]
attribute_tags  → metadata["attribute_tags"]
as_of_timestamp → metadata["as_of_timestamp"]
source_table    → metadata["source_table"]
```

**Adapter contract:**

```python
def retrieve(
    query: str,
    *,
    base_url: str,
    top_k: int = 8,
    filters: dict | None = None,
) -> list[EvidenceItem]:
    """
    Call PipeHouse read endpoint. Normalize scores to [0.0, 1.0].
    Populate source_store="pipehouse", source_ref=record_id.
    Return empty list on any exception; caller handles unavailability.
    """
```

---

## Score normalization

Each store's results are normalized independently before merging:

```python
def _normalize_scores(items: list[EvidenceItem]) -> list[EvidenceItem]:
    scores = [i.score for i in items if i.score is not None]
    if not scores or max(scores) == min(scores):
        return items
    lo, hi = min(scores), max(scores)
    for item in items:
        if item.score is not None:
            item.score = (item.score - lo) / (hi - lo)
    return items
```

After per-store normalization, all items are merged into a single list and sorted
by `score` descending. No cross-store weighting is applied in the first cut — equal
treatment across stores. A `CORE_MEMORY_STORE_WEIGHTS` env var (comma-separated floats
for `core_memory,pipehouse`, defaulting to `1.0,1.0`) provides a post-normalization
multiplier if the user wants to tune. Legacy `core_memory,ragie,pipehouse` values are
tolerated by ignoring the retired middle slot and preserving the third value as PipeHouse.

---

## Unifying ID

When a source is represented in both Core Memory and an external evidence source, a shared
ID links them at answer time.

**At ingest time:**
- The Core Memory transcript bead stores the unifying ID in
  `bead.links["core_memory_unifying_id"] = "<id>"`.
- The external source carries the same `core_memory_unifying_id`.

**At retrieval time:**
The fan-out combination layer checks whether any two items (one from Core Memory,
one external) share the same `core_memory_unifying_id`. If so, they are grouped:
the Core Memory bead is the primary item; the external item is attached as
`EvidenceItem.metadata["unified_with"] = [external_item.source_ref]`. The external item
is removed from the top-level evidence list (deduplicated into the primary).

This grouping is applied after normalization and before final ranking.

---

## Fan-out orchestration (`retrieval/fanout.py`)

```python
def fanout_recall(
    query: str,
    *,
    core_memory_result: RecallResult,
    pipehouse_cfg: dict | None,
) -> RecallResult:
    """
    Fan out to configured external stores in parallel (ThreadPoolExecutor, timeout=5s).
    Normalize per-store scores. Resolve unifying IDs. Merge and re-rank.
    Populate unavailable_stores on timeout or exception.
    Returns the augmented RecallResult.
    """
```

Each external call runs in its own thread with a 5-second timeout. Timeout or
exception → store appended to `unavailable_stores`, empty result list used.

---

## Implementation tasks

1. **`retrieval/contracts.py`** — Add `source_store`, `source_ref`, `unifying_id` to
   `EvidenceItem`. Add `unavailable_stores` and `fanout_stores` to `RecallResult.metadata`
   population in `recall_result_from_memory_execute()`.

2. **`retrieval/adapters/__init__.py`** — Create package.

3. **`retrieval/adapters/pipehouse_adapter.py`** — Implement `retrieve()` per contract above.

4. **`retrieval/fanout.py`** — Implement `fanout_recall()` per contract above.
   `_normalize_scores()`, unifying ID grouping, `ThreadPoolExecutor` parallel dispatch.

5. **`retrieval/agent.py`** — In `recall()`, after Core Memory `memory_execute()` returns,
   check for configured adapters. If present, call `fanout_recall()` and return the
   augmented result. If no adapters configured, return Core Memory result unchanged.

6. **`config/feature_flags.py`** — Add `CORE_MEMORY_PIPEHOUSE_URL`,
   `CORE_MEMORY_STORE_WEIGHTS` env var reads. Document them alongside existing flags.

7. **Tests** — Three fixtures:
   - Fan-out with PipeHouse returning results → merged, normalized evidence list
   - PipeHouse times out → `unavailable_stores=["pipehouse"]`, Core Memory results present
   - Two items share `core_memory_unifying_id` → grouped, external item deduplicated into primary

---

## Dependencies / risks

- **5-second timeout** is a guess for acceptable latency. Measure PipeHouse p95 latency on
  the subscription tier in use and adjust before shipping.
- **`httpx` dependency** — verify it is already in the dependency graph or use
  `urllib.request` to avoid adding a new dep.
