# CLAUDE.md — Core Memory 2.0 (Rust engine)

The Python 1.x implementation lives on the `v1.x` branch. This tree is the
Rust rewrite. Do not port 1.x code here; port ideas only when the engine's
scope below calls for them.

---

## What this repo is

The Core Memory engine turns any stream of source events into durable,
auditable memory records. For each SourceEvent it:

1. admits the event with its exact bytes and a stable identity;
2. cuts it into deterministic Chunks;
3. attaches deterministic facets;
4. assigns a type (profile rules, then a stock model, else `pending`);
5. optionally judges evidence-backed relationships between chunks;
6. delivers versioned Records to every configured sink.

Interpretation that evolves (reclassification, long-range relationships,
claims, validity, consolidation) happens downstream, in memoriesQL and the
Dreamer. It is appended as new versioned rows and never rewrites source.

memoriesQL is the priority sink. No change here may break it.

---

## Guiding principles

- **The full chunk text is the memory.** No summaries, gists, rewrites or
  framing text ever enter a Record. Naive retrieval over raw text matches or
  beats transformed memory stores.
- **Boring primitives, rich views.** Events, chunks, facets, type decisions
  and relationships are the primitives. Worldlines, tensions, storylines and
  similar ideas are views computed downstream, not storage concepts here.
- **The flat-file test.** Before building a feature, ask whether Markdown,
  metadata, embeddings and a periodic summarizer would do. Build it here only
  when that version fails a contested, temporal, distributed or
  action-relevant question.
- **Honest outcomes.** Every stage reports a typed outcome. `pending`
  (retryable) is never conflated with `unassigned` (a final judgment), and
  the engine never fabricates data to satisfy a sink.

---

## Invariants — never violate these

### 1. Crate layering

```
core-memory-contract -> core-memory-chunker -> core-memory-core
    -> core-memory-store-* / core-memory-source-* / core-memory-sink-*
    -> core-memory-sidecar, core-memory (facade)
```

- `core-memory-contract` depends on no other crate in this workspace.
- `core-memory-chunker` may depend only on `core-memory-contract`.
- `core-memory-core` imports no store, source, sink or model-provider crate.
  `scripts/check_layering.py` enforces this in CI.
- Core has no code path specific to one sink. Sinks differ only through the
  capabilities they declare.

### 2. Identity

- `event_key = (scope, source_ns, external_id, revision)`. Never positional,
  never derived from ingest order.
- The identity check compares the event-level hash before chunking, so a
  chunker change never looks like an identity conflict.
- Chunk IDs derive from the event key, the chunker ID and revision, and the
  byte range. Never from a global index.
- Idempotency keys are never evicted. Erasure turns them into tombstones
  matched by a keyed hash.

### 3. Determinism

- Chunk boundaries, chunk IDs and facets are pure functions of their inputs
  and versioned configuration. Changing behavior means bumping a revision.
- The clock, ID source, model transport and operational store are injected,
  never global.
- Chunks partition the source text exactly: concatenated in order they
  reproduce it byte for byte.

### 4. Relationships are judged, never inferred

A relationship is never created from similarity, chronology or shared keys
alone. Structural links (reply-to, parent, revision-of) are facets and
candidate seeds, not relationships.

### 5. Delivery is at-least-once through the outbox

Admission writes the ledger entry and its records in one transaction. Sinks
read from the outbox with their own cursors. Optional sinks never pin
operational state.

---

## Working in this repo

```bash
cargo fmt --all
cargo +1.99 clippy --workspace --all-targets -- -D warnings   # CI's lint toolchain
cargo test --workspace
python3 scripts/check_layering.py
```

Requirement IDs in comments (ING-03, CHK-04, REC-01, ...) refer to the Core
Memory Engine PRD v1.1.
