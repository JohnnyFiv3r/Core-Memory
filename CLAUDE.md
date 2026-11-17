# CLAUDE.md — Core Memory 2.0 (Rust engine)

The Python 1.x implementation lives on the `v1.x` branch. This tree is the
Rust rewrite. Do not port 1.x code here; port ideas only when the engine's
scope below calls for them.

`docs/architecture.md` holds the design behind the invariants in this file.
Sections it marks *planned* are designed and not built yet.

---

## What this repo is

The Core Memory engine turns any stream of source events into durable,
auditable memory in PostgreSQL. For each SourceEvent it:

1. admits the event with its exact bytes and a stable identity;
2. cuts it into deterministic chunks and attaches deterministic facets;
3. commits the event, its chunks, their text and its beads in one
   transaction;
4. asks a model for each chunk's type and where its entities, actions and
   outcomes are, when a reader is configured and the chunk's scope allows
   the model's destination;
5. shortens each tool call by fixed rules and asks a model what the call
   did and what came of it, when a tool reader is configured and the call's
   scope allows the model's destination;
6. *planned:* asks a judge model how new beads relate to recent ones.

PostgreSQL is the only store and the system of record. The engine owns the
`core_memory` schema. A host such as memoriesQL runs the engine against its
own database, governs it through SQL functions, and reads its tables.
Interpretation that evolves over long ranges happens in the host and reaches
the store through the engine as new rows. Nothing rewrites source.

memoriesQL is the priority host. No change here may break what it reads or
calls.

---

## Guiding principles

- **The full chunk text is the memory.** No summaries, gists, rewrites or
  framing text ever stand in for it. Naive retrieval over raw text matches or
  beats transformed memory stores.
- **Boring primitives, rich views.** Events, chunks, facets, beads, type
  decisions and relationships are the primitives. Worldlines, tensions,
  storylines and similar ideas are views computed downstream, not storage
  concepts here.
- **The flat-file test.** Before building a feature, ask whether Markdown,
  metadata, embeddings and a periodic summarizer would do. Build it here only
  when that version fails a contested, temporal, distributed or
  action-relevant question.
- **Honest outcomes.** Every stage reports a typed outcome. `pending`
  (retryable) is never conflated with `unassigned` (a final judgment),
  `unsure` never counts as a no, and the engine never fabricates data to
  satisfy a consumer. Input that could be read two ways is refused, not
  interpreted. A failure that would repeat is never reported as one worth
  retrying: a command told to wait for a database that refuses its login
  would wait for good. A caller whose own request is not at fault is still
  told to keep its event and offer it again.

---

## Invariants — never violate these

### 1. Crate layering

```
core-memory-contract -> core-memory-chunker -> core-memory-core
    -> core-memory-store-* / core-memory-source-* / core-memory-model-*
    -> core-memory-sidecar, core-memory (facade)
```

- `core-memory-contract` depends on no other crate in this workspace.
- `core-memory-chunker` may depend only on `core-memory-contract`.
- `core-memory-core` imports no store, source or model-provider crate, and
  no database, network or async-runtime crate.
  `scripts/check_layering.py` enforces this in CI.
- Core has no code path specific to one host or one model provider.

### 2. Identity

- `event_key = (scope, source_ns, external_id, revision)`. Never positional,
  never derived from ingest order.
- The identity check compares the event-level hash before chunking, so a
  chunker change never looks like an identity conflict.
- Event and chunk IDs derive from the event key (and, for chunks, the chunker
  ID and revision and the byte range) with HMAC-SHA-256 under the workspace's
  secret key. Never from a global index.
- A scope belongs to one workspace for good, because its IDs are keyed by it.
- An erased event's ID is its tombstone. It is never reused or evicted, and
  no hash of the erased event is kept.

### 3. Determinism

- Chunk boundaries, chunk IDs and facets are pure functions of their inputs
  and versioned configuration. Changing behavior means bumping a revision.
- The clock, ID source, model transport and store are injected, never global.
- Chunks partition the source text exactly: concatenated in order they
  reproduce it byte for byte.

### 4. Relationships are judged, never inferred

A relationship is never created from similarity, chronology or shared keys
alone. Code computes a judge's inputs and never proposes or writes an edge;
a score is never evidence. Structural links (reply-to, parent, revision-of)
are facets and candidate seeds, not relationships.

### 5. One store, one transaction

- Admission commits the event, its chunks, their text, its beads and the
  reader's checkpoint in one transaction, and only while the scope is active
  inside that transaction. There is no second copy and no delivery step.
- Stored rows are immutable. Only erasing changes them.
- Only the engine writes the `core_memory` data tables. Hosts govern through
  the SQL functions and read through grants and policies of their own. Row
  security stays on for every table and nothing is granted to `PUBLIC`.
- The engine's database role can read and insert. It cannot change or delete
  what it stored, change a scope's state, give model consent, or erase. The
  queue of its own work (`work`) is the one table it changes in place.

### 6. Text lives in one place

- Source text is stored once, in `chunk_text`. Every other table holds IDs,
  ranges, enums and engine-side times, with one exception: `tool_outcomes`
  holds what a model wrote about a tool call, an action of at most 120
  characters and an outcome of at most 200. It is erased with the call.
- Erasing a message removes its text and everything derived from that text,
  in one transaction. A new table that holds content must be erased with its
  source, and must say so in `docs/architecture.md`.
- Logs, error messages, receipts and work queues never hold source text or
  the database URL. An error about the configuration repeats a setting's
  value only once the value has passed that setting's own check, and
  everything the command prints goes out through `report`, which withholds
  a message that repeats a URL.
- An answer never grows with what it refuses: a refusal lists a bounded
  number of problems, each of bounded length.
- The HTTP service checks a caller's token before it reads any of the
  request body, and keeps a connection open between requests only for a
  caller that has shown one.

### 7. One writer per workspace, and rows commit in order

- Every transaction that writes for a workspace locks its change counter
  before it reads anything it will act on: the engine through
  `core_memory.begin_write`, governance functions through
  `core_memory.lock_workspace`. No writer takes a row lock on a scope
  first. One lock, taken first, is what keeps writers from deadlocking and
  keeps a pause or a revocation from being overtaken.
- Every row the engine writes for a workspace, a model call's receipt
  included, carries a `seq` taken from that counter under the lock. A reader that pages by `seq` greater than
  the last one it saw must never miss a row. Never number rows from a
  PostgreSQL sequence or identity column.

### 8. The schema version is fenced

An engine writes only to the schema version it was built for. A released
migration never changes.

Every migration locks `schema_migrations` first, and that lock is the first
one everything else takes, so a call that arrives during a migration waits
holding nothing the migration needs:

- a write starts with `core_memory.begin_write`, which refuses another
  version;
- every function a role may call starts with `core_memory.hold_schema`;
- a read of several statements locks the table before its first query, so
  its snapshot is never older than a migration it waited for;
- a read of one statement has `schema_migrations` first in its FROM
  clause, which is the order PostgreSQL locks in. Naming it first in the
  select list is not enough.

Tests fail if a function or a store call takes another lock first.

### 9. SQL that cannot be hijacked

- Every function in the schema sets `search_path = pg_catalog, pg_temp`,
  with `pg_temp` last, and names every object it uses with its schema. A
  test fails if a function does not.
- Every function is revoked from `PUBLIC`. The engine role may execute
  `begin_write` only; the governor role, the governance functions only.
- Engine sessions and migrations set the same search path, and
  `READ COMMITTED`.
- A column that holds what a source said, or a hash of it, is excluded
  from planner statistics (`SET STATISTICS 0`). The source's own time
  (`time_at`) is such a column.

### 10. Nothing is sent without a permit

- Every model call goes through `Store::permit` first. In one transaction
  it checks that the scope is active and has granted the binding's
  destination, checks the budget, reads the text and writes the call's
  receipt. The receipt is committed before any text leaves the engine. A
  refusal sends nothing.
- The destination is derived from the binding's endpoint, never written by
  hand. Consent is per scope and per destination, and only a governance
  function gives it.
- Every call goes through the `ModelTransport` trait: one request, with no
  hidden retry or fallback. Core has no code for any one provider.
- Nothing a model writes about a chunk is stored. A type is a key of the
  vocabulary, an abstention is one of a fixed set, and anything else the
  model reports is a quote kept as the byte range where it was found in
  the chunk.
- What a model writes about a tool call is stored in `tool_outcomes` and
  nowhere else: two bounded lines and a status from a fixed set. A model
  never sees a tool call's stored text whole. Code shortens it first, by
  rules that are fixed and versioned (`core-memory-core::compress`), and
  the receipt counts the bytes that were sent.
- An abstention is a judgment and is stored. It is never retried as if the
  model had not answered, and never counted as a wrong answer.
- A read is stored only for a chunk its call's receipt names, whose text
  still exists and whose scope is still active. A tool outcome is stored
  only for the event the receipt's chunks belong to, on the same terms.
- The queue, the receipts, the results and the logs hold IDs, counts and
  problem codes. Never source text, and never what a model or a provider
  said.
- A bead of a tool call is the call's event: one bead, with the event's ID,
  however many chunks its text has.

---

## Working in this repo

```bash
cargo fmt --all
cargo +1.99 clippy --workspace --all-targets --all-features -- -D warnings   # CI's lint toolchain
cargo test --workspace
python3 scripts/check_layering.py
```

Tests that touch the store need a PostgreSQL server (15 or newer) and run
behind the `postgres-tests` feature. Each test creates and drops its own
database:

```bash
export CORE_MEMORY_TEST_DATABASE_URL=postgresql://postgres:secret@127.0.0.1:5432/postgres
cargo test --workspace --all-features
```

There is no in-memory store. The engine's guarantees are transactional, so
they are tested against PostgreSQL.

Requirement IDs in comments (ING-03, CHK-04, REC-01, ...) refer to the Core
Memory Engine PRD v1.1. Where the PRD and `docs/architecture.md` differ, the
architecture document is current.
