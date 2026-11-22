<p align="center">
  <img src="https://raw.githubusercontent.com/JohnnyFiv3r/Core-Memory/main/docs/assets/core-memory-hero-banner.jpg" alt="Core Memory banner" />
</p>

<p align="center">
  <a href="https://github.com/JohnnyFiv3r/Core-Memory/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="Apache-2.0 License"></a>
  <a href="https://pypi.org/project/core-memory/"><img src="https://img.shields.io/pypi/v/core-memory.svg" alt="PyPI version"></a>
  <a href="#quick-start"><img src="https://img.shields.io/badge/PostgreSQL-15%2B-336791.svg" alt="PostgreSQL 15+"></a>
</p>

<p align="center">
  <b>Durable, auditable memory for AI agents.</b><br>
  Every event kept verbatim in PostgreSQL, read by a model you choose and related by a judge, so agents recall <i>what was said</i> and <i>what still holds</i>.
</p>

<p align="center">
  <a href="#quick-start">Quickstart</a> · <a href="#features">Features</a> · <a href="#supported-providers-and-hosts">Supported Providers</a> · <a href="#contributing">Contributing</a>
</p>

## Core Memory

Give your AI agents memory that keeps the record straight. Core Memory 2 is a self-hosted memory engine, rewritten from the ground up in Rust. It turns any stream of source events into durable, auditable memory in PostgreSQL: chat messages, documents, CRM record changes and an agent's tool calls. Run it as a command, as an HTTP service, or inside your own database.

Most memory layers summarize what they are given and keep the summary. Core Memory keeps the original. The full text of every event is the memory. What models add is structure on top of that text, never a rewrite in its place.

**Keep every event, exactly:** Each event is admitted with its exact bytes and a stable identity, cut into deterministic chunks, and committed in one transaction. Offer it again and the answer is `replay`, never a duplicate.

**Read by the model you choose:** A model gives each chunk a type from your vocabulary and marks where its entities, actions and outcomes are, as ranges of the chunk's own text. Nothing the model writes is stored in place of the source.

**Related by a judge:** A judge model says how each new memory relates to the ones near it in time, and to the ones its source names: what caused what, what replaced what, and what cannot both hold. It answers in the arrows of Mark Burgess's Semantic Spacetime. A replacement is written only after a second read gives it again, and a contradiction keeps both sides.

**Recall what still holds:** `recall` returns the stored text that holds your words, verbatim. With it comes what replaced that text, what disputes it and what the judge's arrows lead to. All of it comes from the same tables, with no second store.

---

## Quick Start

Install the command. Each wheel carries the compiled engine for Linux or macOS, so no Rust toolchain is needed:

```bash
uv tool install core-memory     # or: pipx install core-memory
core-memory --version           # core-memory 2.0.1
```

Start PostgreSQL 15 or newer:

```bash
docker run -d --name core-memory-pg -e POSTGRES_PASSWORD=secret \
    -p 127.0.0.1:5432:5432 postgres:18
export CORE_MEMORY_DATABASE_URL=postgresql://postgres:secret@127.0.0.1:5432/postgres
```

Store the example events and recall from them:

```bash
git clone https://github.com/JohnnyFiv3r/Core-Memory
cd Core-Memory/examples/quickstart

core-memory migrate                            # create the core_memory schema
core-memory scope add acme --workspace demo    # allow events for scope "acme"
core-memory ingest --input events.jsonl        # one typed outcome per event
core-memory recall phased cutover              # the stored text that holds those words
```

That stores four events: two Slack messages, a Salesforce opportunity and a planning doc. The recall returns the two that mention the phased cutover, word for word.

To have a model read what is stored, add a `[reader]` and a `[[binding]]` to `core-memory.toml`. Then let the scope send its text to that model:

```bash
core-memory scope allow acme --binding small   # consent, for this scope and this model's host
core-memory read run                           # read what is due now, then stop
```

See the [full guide](https://github.com/JohnnyFiv3r/Core-Memory/blob/main/docs/guide.md) for:
- the HTTP service;
- model providers, including your ChatGPT plan;
- tool calls, the judge, recall and erasure;
- running the engine inside your own database.

---

## Features

**Verbatim memory:** The full chunk text is the memory. The engine writes no summaries, gists or framing text in its place. What a model reads in a chunk is stored as a type and as byte ranges of the chunk itself. The one exception is a tool call: beside its full text, the engine keeps two short lines a model wrote about it.

**Stable identity:** An event is known by its scope, source, external ID and revision, never by its position or ingest order. Event and chunk IDs are derived from that key under each workspace's secret key, so they are the same on every replay and reveal nothing about what they name.

**Typed outcomes, never guesses:** Every event gets one of `accepted`, `replay`, `identity_conflict`, `checkpoint_conflict`, `refused` or `busy`. Input that could be read two ways is refused, not interpreted. `busy` means keep the event and send it again.

**Consent before any model call:** A scope's text goes to a model only after that scope allows the model's destination. Every call is permitted in the same transaction that reads the text, and held to the binding's daily budget if it has one. Its receipt is committed before anything is sent.

**Models you choose:** Anthropic, OpenAI, OpenAI-compatible servers such as a local model, or OpenAI's models on your own ChatGPT Plus or Pro plan. Any of them can be the reader, the tool reader or the judge.

**Tool calls as memory:** An agent's tool call is stored whole. Before a model reads it, fixed rules shorten it. The model writes two short lines about it: what the call did and what came of it.

**Judged, never inferred:** No relationship is made from similarity, chronology or shared keys alone. Code only chooses what the judge is shown, and it chooses by time and by what a source names. Every pair the judge sees gets a verdict, and "cannot tell" is never a no.

**Recall with provenance:** Every match comes back with its event key and its time, and says where that time came from. It also says whether the match is still current and whether another statement disputes it.

**Erasure that reaches everything:** Erasing a message removes its text and everything derived from it, in one transaction. Its ID stays as a tombstone, and the erasure is written to a journal.

**Built to live in your database:** The engine uses one PostgreSQL schema. Row security is on for every table, and the engine's own role is least-privilege. Hosts govern it through SQL functions. Rows commit in a fixed order, so a reader that pages through them never misses one.

**Authenticated HTTP service:** Per-token scope grants, readiness and health checks, and an OpenAPI 3.1 document.

**Export everything:** Events, chunks, reads, tool outcomes, verdicts, edges, name links and erasures, as `core_memory.record.v2` JSON Lines.

**Measured with live models:** 104 messages were written to mislead a reader or a judge. Over three runs, the reader got 193 of 201 answers right and the judge 86 to 87 of 87. See [the evaluations](https://github.com/JohnnyFiv3r/Core-Memory/tree/main/eval).

---

## Supported Providers and Hosts

**Model providers**

| Provider | `provider` | Paid with | Guide |
|---|---|---|---|
| Anthropic | `anthropic` | An API key | [Reading with a model](https://github.com/JohnnyFiv3r/Core-Memory/blob/main/docs/guide.md#reading-with-a-model) |
| OpenAI | `openai` | An API key | [Reading with a model](https://github.com/JohnnyFiv3r/Core-Memory/blob/main/docs/guide.md#reading-with-a-model) |
| OpenAI-compatible servers (a local model, a gateway) | `openai-compatible` | Your own server | [Reading with a model](https://github.com/JohnnyFiv3r/Core-Memory/blob/main/docs/guide.md#reading-with-a-model) |
| OpenAI on your ChatGPT plan | `openai-chatgpt` | Your Plus or Pro plan | [Using your ChatGPT plan](https://github.com/JohnnyFiv3r/Core-Memory/blob/main/docs/guide.md#using-your-chatgpt-plan) |

**Ways to run it**

| Host | How | Guide |
|---|---|---|
| Command line | `core-memory ingest`, `read run`, `recall`, `erase` and `export` | [Try it](https://github.com/JohnnyFiv3r/Core-Memory/blob/main/docs/guide.md#try-it) |
| HTTP service | `core-memory serve`: `POST /v1/events`, `POST /v1/recall`, `GET /v1/openapi.json` | [Over HTTP](https://github.com/JohnnyFiv3r/Core-Memory/blob/main/docs/guide.md#over-http) |
| Inside your own PostgreSQL | The engine owns one `core_memory` schema. Your system governs it through SQL functions and reads its tables, or two read views, through grants and policies of its own, with no copy to keep in step. | [Running inside another system's database](https://github.com/JohnnyFiv3r/Core-Memory/blob/main/docs/guide.md#running-inside-another-systems-database) |

---

```mermaid
flowchart LR
    S[Source events] --> A[Admit<br/>identity, chunks, facets]
    A --> P[(PostgreSQL<br/>core_memory)]
    P --> R[Reader<br/>type and spans]
    P --> T[Tool reader<br/>action and outcome]
    P --> J[Judge<br/>arrows between memories]
    R --> P
    T --> P
    J --> P
    P --> Q[Recall and export]
```

## How It Works

Core Memory keeps its writes deterministic and puts every model call behind a gate. Each event follows the same path:

**Admit:** An event arrives from the command, the HTTP service or a reader that tails a source. The engine checks its identity before cutting it into chunks, so a change to the chunker never looks like a conflict. It then cuts the text into chunks that reproduce it byte for byte and attaches deterministic facets. The event, its chunks, their text and its beads commit in one transaction, and only while the scope is active.

**Read:** With a reader configured, each chunk is queued for a model. The model picks one type from your vocabulary, or abstains and says why. It also quotes the chunk's entities, actions and outcomes. Each quote is kept only as the byte range where it was found, so a quote that is not in the chunk is dropped. With a tool reader configured, each tool call goes to a model that writes what the call did and what came of it.

**Judge:** With a judge configured, each new memory is shown beside the memories near it in time, from every scope of the workspace that allows it, and beside the ones its source names as what it replies to, belongs under or is a revision of, however long ago they were. The judge answers each pair with an arrow, with none, or with "cannot tell". A memory stays current until a later statement replaces or withdraws it. Such a chain never branches, and a contradiction ends neither side. Finding related pairs across the whole store, beyond what the judge reads in time order, is a host's work and not the engine's: memoriesQL does it.

**Recall:** Recall uses PostgreSQL's full-text search over the stored text and makes no model call. A match that was replaced is answered by its successor. Disputed statements are marked, and the judge's arrows are followed up to two steps.

**Erase:** Erasing removes a message's text and everything derived from it in one transaction. The message's ID stays as a tombstone, so links to it never dangle and the same message is refused if it is offered again.

**Core Concepts**

**SourceEvent**
One thing a source said or changed: a message, a document revision, a record change or a tool call. It is known by `(scope, source_ns, external_id, revision)` and stored with its exact bytes.

**Scope and Workspace**
A scope is one connection to one source, such as one Slack workspace or one agent harness. Events are admitted only for a scope that is enrolled and active. A workspace is a set of scopes that belong together. Its secret key is what event and chunk IDs are derived from.

**Chunk and Bead**
A chunk is a deterministic piece of an event's text, and the chunks of an event concatenate back to it exactly. A bead is a unit of memory: a chunk, a tool call as a whole, or a group the judge made of memories that are parts of one.

**Destination and Consent**
A destination is where a model call sends text, taken from the binding's endpoint and never written by hand. A binding paid by one person's ChatGPT plan is a destination of its own. Consent is given per scope and per destination, and only through a governance function.

**Permit and Receipt**
Every model call is permitted first. The permit checks that the scope is active and has allowed the destination, and that the day's budget is not spent. It then reads the text and commits a receipt naming the destination and what was sent. A refusal sends nothing. Receipts, queues and logs hold IDs, counts and problem codes, never text.

**Verdict, Edge and Arrow**
A verdict is recorded for every pair the judge was shown, whatever it said. An edge records an arrow from one memory to another. Arrows come from a fixed directory of four types: `leadsto`, `contains`, `express` and `near`. Edges have no weight and are never changed.

Learn more in the [architecture docs](https://github.com/JohnnyFiv3r/Core-Memory/blob/main/docs/architecture.md).

---

## Recall Example

**Request**

```bash
core-memory recall phased cutover
```

Or over HTTP, with a token that may recall from the scope:

```bash
curl -s localhost:7600/v1/recall -H "Authorization: Bearer $TOKEN" \
    -d '{"query": "phased cutover", "scopes": ["acme"]}'
```

**Response**

This is the real answer for the Quick Start's events. The IDs are the ones the engine derived for them.

```json
{
  "found": "matched",
  "beads": [
    {
      "bead_id": "73ceb7ed-819d-8b25-bbf4-5d75246c2737",
      "kind": "chunk",
      "event": {
        "event_id": "cf46e316-4904-8189-b529-b79110b490d1",
        "key": {
          "scope": "acme",
          "source_ns": "slack/T01",
          "external_id": "C042/1730000001.000100"
        }
      },
      "time_at": "2026-10-20T15:02:11Z",
      "time_basis": "source",
      "via": "match",
      "rank": 0.1,
      "current": true,
      "disputed": false,
      "chunks": [
        {
          "chunk_id": "73ceb7ed-819d-8b25-bbf4-5d75246c2737",
          "ordinal": 0,
          "text": "Billing migration: we're going with the phased cutover. Big bang is too risky with the ledger export as slow as it is."
        }
      ]
    },
    {
      "bead_id": "b2415cab-a7e2-8027-a6dc-8dfb56042f9a",
      "kind": "chunk",
      "event": {
        "event_id": "0b0c1451-1eb8-856f-aa04-b0f5471114b7",
        "key": {
          "scope": "acme",
          "source_ns": "docs/drive",
          "external_id": "cutover-plan.md",
          "revision": "v3"
        }
      },
      "time_at": "2026-10-21T00:00:00Z",
      "time_basis": "source_bucket",
      "via": "match",
      "rank": 0.05,
      "current": true,
      "disputed": false,
      "chunks": [
        {
          "chunk_id": "b2415cab-a7e2-8027-a6dc-8dfb56042f9a",
          "ordinal": 0,
          "text": "# Billing cutover plan\n\nPhase one moves read traffic to the new ledger on November 3.\n\nPhase two moves writes once the export runs in under an hour. Owner: OPS-412.\n"
        }
      ]
    }
  ],
  "arrows": [],
  "linked_omitted": 0
}
```

With a judge configured, the answer carries what the judge said:
- `arrows` lists the judge's arrows between the beads returned.
- A match that a later statement replaced is answered by that statement.
- `disputed` marks a statement that another current statement contradicts.

---

## Documentation

**Repo Map**
```
crates/
├── core-memory-contract/         SourceEvent, outcomes, IDs and records
├── core-memory-chunker/          deterministic, lossless chunking
├── core-memory-core/             the pipeline, and the Store and ModelTransport traits
├── core-memory-store-postgres/   the PostgreSQL store, its schema and migrations
├── core-memory-source-jsonl/     SourceEvents read from JSON Lines
├── core-memory-model-http/       what the model transports share
├── core-memory-model-anthropic/  Anthropic
├── core-memory-model-openai/     OpenAI and OpenAI-compatible servers
├── core-memory-model-chatgpt/    OpenAI on your ChatGPT plan
├── core-memory-sidecar/          the core-memory command and HTTP service
└── core-memory/                  facade re-exporting the above
```
Other useful folders:

* examples/ the quickstart's events and config
* eval/ the live-model evaluations and their results
* fixtures/ golden exports the tests compare against
* docs/ the guide, the architecture and what became of 1.x
* scripts/ the crate layering check CI runs

Useful docs:

[Guide](https://github.com/JohnnyFiv3r/Core-Memory/blob/main/docs/guide.md)
[Architecture](https://github.com/JohnnyFiv3r/Core-Memory/blob/main/docs/architecture.md)
[What became of 1.x](https://github.com/JohnnyFiv3r/Core-Memory/blob/main/docs/lineage.md)
[Releasing](https://github.com/JohnnyFiv3r/Core-Memory/blob/main/RELEASING.md)

---

## Contributing

To build the command from source you need Rust 1.89 or newer:

```bash
git clone https://github.com/JohnnyFiv3r/Core-Memory.git
cd Core-Memory
cargo build --release      # the command is target/release/core-memory
```

The checks CI runs:

```bash
cargo fmt --all
cargo +1.99 clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace                 # tests that need no database
python3 scripts/check_layering.py
```

Tests that touch the store run against a real PostgreSQL server. Each test creates and drops its own database:

```bash
export CORE_MEMORY_TEST_DATABASE_URL=postgresql://postgres:secret@127.0.0.1:5432/postgres
cargo test --workspace --all-features
```

---

## Maintainers

Core Memory is maintained by:

[John Inniger](https://github.com/JohnnyFiv3r) (@JohnnyFiv3r)
[Chris Dedow](https://github.com/chrisdedow) (@chrisdedow)

For bugs and feature requests, please open an issue. For anything else related to the project, feel free to reach out to the maintainers directly.

---

## Looking for Core Memory 1.x?

The Python implementation is frozen at 1.1.2. Its code is on the [`v1.x`](https://github.com/JohnnyFiv3r/Core-Memory/tree/v1.x) branch, and it stays on PyPI:

```bash
pip install "core-memory<2"
```

1.x gets security fixes only, made on `v1.x`. [What became of 1.x](https://github.com/JohnnyFiv3r/Core-Memory/blob/main/docs/lineage.md) goes through each 1.x module and says what 2.0 kept, replaced, dropped or left to the host.

---

## Inspiration

Inspired in part by Steve Yegge's writing on beads and memory systems: https://github.com/steveyegge/beads

The judge's relationships are the arrows of Mark Burgess's Semantic Spacetime, as his SSTorytime names them: https://github.com/markburgess/sstorytime

---

<p align="center">
  <a href="https://github.com/JohnnyFiv3r/Core-Memory/blob/main/LICENSE">Apache-2.0 License</a> · <a href="https://github.com/JohnnyFiv3r/Core-Memory/blob/main/CODE_OF_CONDUCT.md">Code of Conduct</a> · <a href="https://github.com/JohnnyFiv3r/Core-Memory/releases">Releases</a>
</p>
