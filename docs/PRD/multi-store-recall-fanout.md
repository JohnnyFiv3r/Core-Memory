# PRD: Multi-Store Recall Fan-out (Satorid)

**Status:** Spec only — no implementation exists  
**Effort:** ~1 day spec review + ~4 days implementation  
**Depends on:** #16 (external data bead ingest contract) for PipeHouse adapter  
**Ragie API reference:** https://docs.ragie.ai/reference/retrieve (verify field names before implementing)

---

## Problem

`recall()` fans out to Core Memory's internal semantic index only. Documents, images,
video chunks, and relational data insights live in separate stores (Ragie, PipeHouse)
that are entirely invisible to the retrieval pipeline. An agent query returns causal
memory from conversations but cannot surface a supporting document, a data anomaly, or
a video clip that is directly relevant to the same question.

The result: agents answer from partial context. A question about a vendor decision
returns the decision bead but not the COGS anomaly that prompted it, and not the
contract document that sealed it.

---

## User value

- One query surfaces causal memory, document evidence, and relational data in a single
  ranked result set — the agent reasons across all three, not sequentially across three
  separate calls.
- Source provenance is preserved end-to-end: every evidence item carries its origin
  store and store-native ID, traceable back to the original file or DB record.
- A store being unavailable degrades gracefully — Core Memory recall proceeds and the
  caller sees which stores were unreachable.

---

## Architectural invariant

**Core Memory is the causal anchor. Ragie and PipeHouse are evidence sources.**

Ragie chunks and PipeHouse data beads surface as `EvidenceItem` objects whose
`source_store` is tagged accordingly. They do not own causal edges (`led_to`,
`caused_by`, etc.) — those edges exist only between Core Memory beads. The agent
reasons across all three result sets; it does not treat a Ragie chunk or a PipeHouse
bead as a first-class node in the causal graph.

This invariant must be enforced at the result-combination layer, not by convention.
`EvidenceItem` objects from non-Core-Memory stores must not carry a `bead_id` that
points into the Core Memory bead store unless a real bead exists for them (e.g. a
transcript bead that shares a unifying ID with a Ragie chunk — see Unifying ID below).

---

## Current state

| Component | Status |
|-----------|--------|
| `recall()` in `retrieval/agent.py` | Done — Core Memory only |
| Ragie client wrapper | **Missing** |
| PipeHouse read adapter | **Missing** |
| `EvidenceItem.source_store` field | **Missing** |
| `RecallResult.metadata["unavailable_stores"]` | **Missing** |
| Fan-out orchestration in `retrieval/agent.py` | **Missing** |
| Score normalization across stores | **Missing** |
| Unifying ID resolution at combination layer | **Missing** |

---

## Success criteria

1. `recall("why did COGS increase last quarter")` returns evidence items from Core
   Memory (decision bead), Ragie (relevant contract or document), and PipeHouse (the
   COGS anomaly insight) in a single ranked `RecallResult.evidence` list.
2. Each `EvidenceItem` carries `source_store` ("core_memory" | "ragie" | "pipehouse")
   and `source_ref` (the store-native ID — Ragie `chunk_id`, PipeHouse `record_id`,
   or Core Memory `bead_id`).
3. When Ragie is unreachable, Core Memory and PipeHouse results still return.
   `RecallResult.metadata["unavailable_stores"]` lists `["ragie"]`.
4. A video transcript bead in Core Memory and the corresponding video chunk in Ragie
   that share a unifying ID are surfaced as a single grouped result, not two separate
   items, when both are retrieved for the same query.
5. Score normalization is applied before merging: all three stores' scores are
   independently rescaled to [0.0, 1.0] before the combined list is ranked.
6. Fan-out is only activated when at least one external adapter is configured
   (`SATORID_RAGIE_API_KEY` or `SATORID_PIPEHOUSE_URL` env var is set). Without
   configuration, `recall()` behaves identically to today.

---

## Scope

**In:**
- `retrieval/fanout.py` — fan-out orchestration: parallel calls, result combination,
  score normalization, unifying ID resolution, degraded-mode handling
- `retrieval/adapters/ragie_adapter.py` — Ragie retrieve call → `list[EvidenceItem]`
- `retrieval/adapters/pipehouse_adapter.py` — PipeHouse read call → `list[EvidenceItem]`
- `EvidenceItem` schema additions: `source_store`, `source_ref`
- `RecallResult.metadata["unavailable_stores"]` population
- `recall()` in `retrieval/agent.py` — conditional fan-out when adapters are configured
- Unifying ID grouping at combination layer

**Out:**
- Plugging Ragie into `semantic_index.py` — fan-out is at the recall endpoint layer
- Changes to Core Memory's internal FAISS/Qdrant/pgvector index
- Causal edge creation for Ragie or PipeHouse items
- Multi-hop causal traversal across store boundaries
- Ragie or PipeHouse as write destinations (read-only adapters only in this slice)

---

## Result envelope additions

### `EvidenceItem` additions (`retrieval/contracts.py`)

```python
source_store: str = "core_memory"   # "core_memory" | "ragie" | "pipehouse"
source_ref: str = ""                # store-native ID (chunk_id, record_id, or bead_id)
unifying_id: str | None = None      # cross-store join key when present
```

### `RecallResult.metadata` additions

```python
metadata["unavailable_stores"] = []  # list of store names that failed during fan-out
metadata["fanout_stores"] = []       # list of stores that were queried
```

---

## Ragie adapter (`retrieval/adapters/ragie_adapter.py`)

**Field names are exact per the Ragie OpenAPI spec (`POST /retrievals`).**

The retrieve response is `{"scored_chunks": [ScoredChunk]}`. Each `ScoredChunk` carries:

```
id                — chunk ID → maps to EvidenceItem.source_ref
document_id       — parent document identifier
document_name     — document display name
score             — relevance float (Ragie scale; normalize before merging)
text              — chunk content → maps to EvidenceItem.content_excerpt
index             — chunk position within the document
metadata          — chunk-level custom metadata dict (freeform, additionalProperties)
document_metadata — document-level custom metadata dict (freeform, additionalProperties)
                    store core_memory_unifying_id here at upload time (see Unifying ID)
links             — dict[str, {href: str, type: str}] — source file URLs included
                    in the retrieve response directly; no separate call needed
```

Enable `rerank: true` in the request for better precision at the cost of latency.
This is recommended when Core Memory recall is also running (the combined result set
benefits from reranking).

**Adapter contract:**

```python
def retrieve(
    query: str,
    *,
    api_key: str,
    top_k: int = 8,
    rerank: bool = True,
    partition: str | None = None,
    filter: dict | None = None,
) -> list[EvidenceItem]:
    """
    POST /retrievals with bearer auth. Normalize scores to [0.0, 1.0] (min-max
    over this result set). Map each ScoredChunk to EvidenceItem:
      source_store = "ragie"
      source_ref   = chunk.id
      content_excerpt = chunk.text
      score        = normalized chunk.score
      unifying_id  = chunk.document_metadata.get("core_memory_unifying_id")
      metadata     = {
          "document_id":   chunk.document_id,
          "document_name": chunk.document_name,
          "source_links":  chunk.links,    # dict of {href, type}; includes source URL
          "chunk_index":   chunk.index,
      }
    Return empty list on any exception; caller handles unavailability.
    """
```

---

## PipeHouse adapter (`retrieval/adapters/pipehouse_adapter.py`)

PipeHouse exposes a read endpoint (URL configured via `SATORID_PIPEHOUSE_URL`) that
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
treatment across stores. A `SATORID_STORE_WEIGHTS` env var (comma-separated floats
for `core_memory,ragie,pipehouse`, defaulting to `1.0,1.0,1.0`) provides a
post-normalization multiplier if the user wants to tune.

---

## Unifying ID

When a video (or other source) is ingested to both Core Memory (transcript → bead)
and Ragie (video chunks), a shared ID links them at answer time.

**At ingest time:**
- The Core Memory transcript bead stores the unifying ID in
  `bead.links["core_memory_unifying_id"] = "<id>"`.
- The Ragie document is uploaded with
  `document_metadata={"core_memory_unifying_id": "<id>"}` (document-level metadata,
  not chunk-level, so it is present on every chunk from that document).

**At retrieval time:**
The fan-out combination layer checks whether any two items (one from Core Memory,
one from Ragie) share the same `core_memory_unifying_id`. If so, they are grouped:
the Core Memory bead is the primary item; the Ragie chunk is attached as
`EvidenceItem.metadata["unified_with"] = [ragie_item.source_ref]`. The Ragie item
is removed from the top-level evidence list (deduplicated into the primary).

This grouping is applied after normalization and before final ranking.

---

## Fan-out orchestration (`retrieval/fanout.py`)

```python
def fanout_recall(
    query: str,
    *,
    core_memory_result: RecallResult,
    ragie_cfg: dict | None,
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

3. **`retrieval/adapters/ragie_adapter.py`** — Implement `retrieve()` per contract above.
   Use `httpx` (already a dep if present, else `urllib.request`). No Ragie SDK dependency.

4. **`retrieval/adapters/pipehouse_adapter.py`** — Implement `retrieve()` per contract above.

5. **`retrieval/fanout.py`** — Implement `fanout_recall()` per contract above.
   `_normalize_scores()`, unifying ID grouping, `ThreadPoolExecutor` parallel dispatch.

6. **`retrieval/agent.py`** — In `recall()`, after Core Memory `memory_execute()` returns,
   check for configured adapters. If present, call `fanout_recall()` and return the
   augmented result. If no adapters configured, return Core Memory result unchanged.

7. **`config/feature_flags.py`** — Add `SATORID_RAGIE_API_KEY`, `SATORID_PIPEHOUSE_URL`,
   `SATORID_STORE_WEIGHTS` env var reads. Document them alongside existing flags.

8. **Tests** — Three fixtures:
   - Fan-out with both adapters returning results → merged, normalized evidence list
   - Ragie times out → `unavailable_stores=["ragie"]`, PipeHouse + Core Memory results present
   - Two items share `core_memory_unifying_id` → grouped, Ragie item deduplicated into primary

---

## Dependencies / risks

- **Ragie field names are confirmed** from the OpenAPI spec. `ScoredChunk` fields:
  `id`, `score`, `text`, `index`, `metadata`, `document_id`, `document_name`,
  `document_metadata`, `links`. No field name verification needed before implementing.
- **`links` is already in the retrieve response** — it is a `dict[str, {href, type}]`
  on each `ScoredChunk`. No separate `get_chunk_links` call is needed.
- **PipeHouse read endpoint is not yet specified.** The PipeHouse adapter is a placeholder
  until #16 (external data bead ingest contract) defines the read interface. Implement
  the Core Memory + Ragie fan-out first; wire PipeHouse when #16 is complete.
- **5-second timeout** is a guess for acceptable latency. Measure Ragie p95 latency on
  the subscription tier in use and adjust before shipping.
- **`httpx` dependency** — verify it is already in the dependency graph or use
  `urllib.request` to avoid adding a new dep.
