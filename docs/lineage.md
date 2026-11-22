# From 1.x to 2.0

Core Memory 1.x was a Python library and service. Its last release, 1.1.2,
is on the [`v1.x`](https://github.com/JohnnyFiv3r/Core-Memory/tree/v1.x)
branch. Its package, `core_memory/`, was about 83,000 lines of Python in 349 files. They are in 17
packages and seven modules at the top. The top also holds a marker file,
`py.typed`, so eight files in all, and an 18th directory, `data/`, which holds
three JSON files and no Python. Its tests, benchmarks, demos and plugins were more
on top.

2.0 is a different program. It is an engine of about 27,000 lines of Rust,
with 14,000 lines of tests that run against PostgreSQL. It does less than 1.x
did, on purpose: it turns a stream of events into memory that can be
audited, and leaves to the host whatever a host does better.

This document goes through the whole package, every package and every file at
its top, and says what 2.0 did with each:

- **kept**: 2.0 does the same job, rebuilt;
- **replaced**: 2.0 does the job another way;
- **moved to the host**: a host such as memoriesQL does it, reading the
  engine's tables;
- **dropped**: 2.0 does not do it, and no host needs to on its behalf.

Nothing was ported line by line.

## What decided it

Five rules from [`CLAUDE.md`](../CLAUDE.md) settle most rows below.

- **The full text is the memory.** Naive retrieval over raw text matches
  or beats transformed memory. 1.x wrote summaries, rationales and
  agent-authored rewrites beside the source. 2.0 writes none: a type is a
  key of the host's vocabulary, and anything a model points to is stored as
  a byte range of the source.
- **Boring primitives, rich views.** 1.x stored worldlines, storylines,
  junctions and souls as data. 2.0 stores events, chunks, beads, reads,
  verdicts and edges. Everything else is a view that a host computes.
- **Relationships are judged, never inferred.** 1.x built and weighted
  edges from similarity, co-occurrence and use. In 2.0 a model judges each
  pair it is shown, and code writes what it said. An edge has no weight and
  never changes.
- **One store.** 1.x could write to JSON files, SQLite, Neo4j, Kùzu,
  Graphiti and Qdrant. 2.0 writes to PostgreSQL alone, in one transaction
  per admission.
- **Honest outcomes.** Every stage reports a typed outcome:
  - `pending` is never `unassigned`;
  - `unsure` is never a no;
  - a failure that would repeat is never called worth retrying.

## Module by module

Line counts are of the Python in `core_memory/` at 1.1.2. A file is counted in one row only.

### The memory itself

| 1.x | Lines | In 2.0 |
|---|---|---|
| `write_pipeline`, `runtime/ingest`, `runtime/turn`, `runtime/session`, `runtime/engine.py`, `transcript_ingest.py` | 7,300 | **Replaced.** Any source becomes a `SourceEvent`, admitted with its exact bytes and a stable identity, `(scope, source_ns, external_id, revision)`. A replay is recognised; a changed event under the same key is an identity conflict. There are no sessions or turns: those are what a source's events say, not what the engine stores. |
| Chunking inside `write_pipeline` | — | **Kept, rebuilt.** The chunker is deterministic and lossless: the chunks of an event, joined in order, are its text byte for byte. Chunk IDs come from the event key and the byte range. |
| `persistence` (store, events, backends, turn archive) | 8,200 | **Replaced.** PostgreSQL is the only store. Rows are immutable, and every table has row security. A least-privilege engine role can read and insert, and nothing more. Every row of a workspace carries a sequence number taken under one lock, so a reader that pages by it misses nothing. |
| `schema` | 2,300 | **Replaced** by the `core-memory-contract` crate: `SourceEvent`, typed outcomes, and the `core_memory.record.v2` records an export writes. `core-memory schema record` prints them as JSON Schema. |
| `temporal` | 90 | **Kept** as the source's own time on each event. A judge is shown a bead's neighbours by that time. |

### Reading what was said

| 1.x | Lines | In 2.0 |
|---|---|---|
| `policy/bead_typing`, `policy/bead_judge` | 700 | **Replaced.** A model reads chunks in batches and gives each one a type from the host's vocabulary, or abstains: `no_fit`, `insufficient_evidence` or `ambiguous`. An abstention is a judgment, stored and never retried. The vocabulary is the host's: memoriesQL's 12 bead types at its schema 60. |
| Entity and action extraction | — | **Replaced.** The same read quotes the chunk's entities (of memoriesQL's 10 entity kinds), actions and outcomes. A quote is kept only as the byte range where it was found. |
| `entity` (registry, merge, speaker resolution) and `persistence/entity_*` | 1,200 | **Split.** The judge links two names that stand for one thing, as two byte ranges. Keeping a registry of entities and merging them is **moved to the host**. |
| Tool calls, spread over `runtime/turn` and `integrations` | — | **New in 2.0.** A tool call is stored whole and is one bead. Code shortens it by fixed rules, and a model writes what the call did and what came of it in two bounded lines: the one text the engine stores that a model wrote. |

### How memories relate

| 1.x | Lines | In 2.0 |
|---|---|---|
| `association`, `runtime/associations`, `policy/association_inference_v21` | 5,700 | **Replaced** by the window judge. A model reads each new bead with the beads near it in time and says how each pair relates. Code decides only what it is shown. Every pair shown gets a verdict row, `none` and `unsure` included. A call needs a permit, which checks consent and budget and writes a receipt before any text leaves. |
| `claim` (supersede, retract, reaffirm, conflict) and `persistence/store_claim_ops` | 2,400 | **Replaced** by Mark Burgess's Semantic Spacetime arrows, from a fixed directory. `repl` and `withdraws` end a statement's currency. `confirms` reaffirms it. `!contradicts` keeps both sides current and the dispute open. An arrow that ends currency is written only after a second read, made without the first, gives it again. A chain never branches. Claim slots and answer policy are **moved to the host**. |
| `graph` (core, worldlines, storylines, junctions, traversal) | 4,900 | **Moved to the host as views.** Edges are judged and stored as IDs. A worldline or storyline is a query over them. Root-cause analysis and roadmaps (`graph/root_cause`, `graph/roadmap`) are uses of the graph, not parts of the memory. |
| `graph/edge_weights` and myelination (`persistence/myelination_rewards`, the daily job) | 900 | **Dropped.** An edge has no weight and is never changed. If use should strengthen anything, a host can count use in a view. |
| Graph backends: Neo4j, Kùzu, Graphiti (`persistence/graph`, `integrations/neo4j`) | 1,800 | **Dropped.** PostgreSQL is the store. |
| `runtime/dreamer` and its candidate store (finding associations across the whole store, offline) | 4,900 | **Moved to the host.** memoriesQL finds candidate pairs across the whole store, and they reach the store through the engine as new rows. The engine's judge reads only within a time window. |

### Around the memory

| 1.x | Lines | In 2.0 |
|---|---|---|
| `retrieval` (semantic index, Qdrant, rerank, causal recall, agent) | 11,200 | **Replaced in part; the rest moved to the host.** The engine's recall finds stored text by its words with PostgreSQL's full-text search and returns it verbatim. It leaves out what a judge marked replaced or withdrawn, marks what is disputed, and follows the judge's arrows. It keeps one index on `chunk_text` and no second store: no embeddings, no reranking. Retrieval strategies beyond that are a host's: memoriesQL reads the engine's tables. |
| `soul` (agent identity, tensions, injection) | 3,500 | **Moved to the host application.** An agent's identity is the application's, not the memory's. |
| `runtime/goals` | 900 | **Split.** A goal and its updates are one bead type of the host's vocabulary (`goal`). The judge relates an update to its goal: `implem`, `withdraws`, `repl`. Tracking progress is a host view. |
| Agent-authored memory: `policy/turn_memory_authoring`, `schema/agent_authored_updates`, `runtime/turn/reauthoring`, `policy/rationale` | 2,600 | **Dropped.** The engine never stores generated text in place of what was said. A host that lets agents author memory governs that itself; memoriesQL does. |
| `runtime/queue`, `runtime/passes`, `runtime/flush`, `runtime/post_write`, `runtime/semantic_tasks` and their policies | 4,000 | **Replaced** by one work queue in PostgreSQL. Work is claimed with `SKIP LOCKED` under a lease, its attempts are counted, and failed work is set aside for an operator. Each kind of work runs in lanes of its own. |
| `runtime/observability`, `reporting` | 1,400 | **Replaced.** A receipt for every model call, a result for every receipt, round logs of counts and problem codes, and `read status`. No log holds source text. |
| `policy/hygiene`, `policy/incidents`, `runtime/hygiene` | 500 | **Dropped.** Nothing in the store is cleaned up. Erasure is the one way a row changes. |
| `integrations` (MCP, OpenClaw, PydanticAI, LangChain, CrewAI, GitHub, Obsidian, Spring AI, remote) | 8,500 | **Replaced.** There is an authenticated HTTP service, JSON Lines ingest, and an export of every record. An adapter for a framework belongs to that framework's host. |
| `integrations/http` | 2,800 | **Replaced** by `core-memory serve`. It checks a token before it reads a request's body, grants scopes per token, has a readiness check, and publishes an OpenAPI document. |
| `cli`, `management` | 4,800 | **Replaced** by the `core-memory` command: `migrate`, `workspace`, `scope`, `token`, `ingest`, `read`, `erase`, `export`, `serve`, `check` and `schema`. |
| `config`, `llm_client.py`, `provider_config.py` | 800 | **Replaced** by bindings. A binding pins a model and records the binding's revision with every read. It has a daily budget, and consent is per scope and per destination. There are transports for Anthropic, OpenAI and OpenAI-compatible servers. |
| `persistence/promotion_service`, `persistence/calibration`, promotion in `schema` and `policy` | 1,500 | **Dropped.** Deciding what matters more is retrieval's job, in the host. |

### The public surface, and what ships with it

The files at the top of the package, and its data. Line counts are as
above.

| 1.x | Lines | In 2.0 |
|---|---|---|
| `memory.py`: `Memory`, `MemorySession`, `capture`, and the bead governance calls `confirm_bead`, `request_approval`, `approve_bead`, `reject_bead` and `list_pending_approvals` | 200 | **Replaced.** `capture` was the one write call for a conversation turn. In 2.0 a source gives the engine events (`core-memory ingest`, or the HTTP service), and there is no session object. Confirming and approving a bead set an authority and a confidence class on it. 2.0 has no authority or confidence class, and a stored row changes only when it is erased. A host that wants approvals keeps them in its own tables, beside the engine's, and governs scopes through the SQL functions. |
| `__init__.py`: the package's root exports (`Memory`, `recall`, `write_turn_finalized`, `maintain`, `remove_bead`, the backends and the recall contracts) | 231 | **Replaced** by the `core-memory` facade crate and the command line. Each export has its row above: writing, recall, erasure (`remove_bead`, `remove_source` and `tombstone_bead` become `erase`) and the backends (dropped). |
| `identifiers.py`: `validate_archive_id` | 19 | **Dropped.** It checked a session or turn ID, and refused one with a path separator, a traversal marker or an odd character, before 1.x's file archives used it in a filename. 2.0 has no files of that kind: an ID is a value in a row, and the chunk and event IDs are computed from the event key. |
| `llm_client.py`, `provider_config.py` | (in the `config` row) | **Replaced** by bindings. |
| `transcript_ingest.py` | (in the first table) | **Replaced** by ingest. |
| `_version.py`, `py.typed` | 4 | **Dropped** with the Python package. The workspace's crates carry the version. |
| `data/incidents.json` | — | **Dropped** with `policy/incidents`: named incidents of 1.x's own development, used to tag beads. |
| `data/structural_relation_map.json` | — | **Replaced.** It listed the relation names 1.x's graph accepted (`caused_by`, `supports`, `supersedes` and so on). 2.0 has a fixed directory of arrows, `core_memory.arrows`, and a judge writes them. |
| `data/topic_aliases.json` | — | **Moved to the host.** Aliases for a topic were part of 1.x's query normalisation. Recall in 2.0 matches words, and widening a query is the host's. |

## What 2.0 has that 1.x did not

- **Erasure** of a message, and of everything derived from its text, in
  one transaction. The erased event's ID stays as a tombstone, so it is
  never stored again.
- **A permit before any text leaves.** Each scope consents to each model
  destination. A call is checked against its day's budget, and its receipt
  is committed first.
- **Identity that does not depend on order.** IDs come from the event key
  under the workspace's secret key, not from a counter.
- **A fenced schema.** An engine writes only to the schema version it was
  built for, and waits, holding nothing, while a migration runs.
- **Recall that knows what is current.** A search over the stored text that
  leaves out a statement a judge marked replaced and gives what replaced
  it, and marks a disputed one, from the same tables in one read.
- **Typed outcomes everywhere.** Examples: `replay`, `identity_conflict`,
  `scope_not_active`, `call_in_doubt`, `endpoint_untrusted`.
- **Measured quality.** The hard cases in [`eval/adversarial`](../eval/adversarial/)
  score the reader and the judge against written answers, three runs at a
  time. [`eval/compare`](../eval/compare/) compares judges with each other.

## Where 1.x stands

1.x stays on the `v1.x` branch, frozen at 1.1.2, and on PyPI as
`core-memory<2`. The plan for retiring it is with the owner.
