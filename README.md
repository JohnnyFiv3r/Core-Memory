<p align="center">
  <img src="docs/assets/core-memory-hero-banner.jpg" alt="Core Memory banner" />
</p>

# Core Memory 2.0 (in development)

Core Memory 2.0 is a ground-up rewrite in Rust. It turns any stream of source
events into durable, auditable memory records: it admits each event with its
exact bytes and a stable identity, cuts it into deterministic chunks, attaches
deterministic facets, assigns a type, optionally judges evidence-backed
relationships between chunks, and delivers versioned records to the sinks you
configure (memoriesQL, JSONL, webhooks).

The full chunk text is the memory. The engine never writes summaries or other
generated text into a record.

This branch is pre-release. Nothing from it is published to PyPI or crates.io
yet.

## Try it

You need Rust 1.89 or newer.

```bash
cargo build --release
cd examples/quickstart
../../target/release/core-memory ingest --input events.jsonl
```

`ingest` prints one typed outcome per input line (`accepted`, `replay`,
`identity_conflict`, `refused` or `busy`) and writes one record per chunk to
`records.jsonl`. Run it again and every line comes back as `replay`, with no
new records.

`core-memory schema record` prints the record contract as JSON Schema.

### Over HTTP

`core-memory serve` runs the engine as a sidecar. Generate a token for each
caller; the config keeps only its SHA-256:

```bash
core-memory token --id slack-ingest   # prints the token and a [[http.token]] block
```

```toml
[http]
listen = "127.0.0.1:7600"

[[http.token]]
id = "slack-ingest"
sha256 = "<from core-memory token>"
scopes = ["acme"]
```

| Endpoint | |
|---|---|
| `POST /v1/events` | Admit one SourceEvent. The body is the typed outcome; the status maps it (200 accepted or replay, 409 identity conflict, 403 scope denied, 410 tombstoned, 413 oversize, 422 invalid profile, 400 malformed, 503 busy with `Retry-After`). |
| `POST /v1/events/batch` | `{"events": [...]}`, one outcome per event, in order. |
| `GET /v1/ready` | 503 while the slowest required sink is too far behind, so hosts can pause intake. |
| `GET /v1/openapi.json` | OpenAPI 3.1, generated from the contract types. |

A background thread delivers records to the sinks. On SIGTERM the server
stops accepting requests and delivers what it admitted before exiting.

## What works today

- **Admission** with stable identity: `(scope, source_ns, external_id,
  revision)`. Replays, identity conflicts, tombstones, scope grants, size
  limits and backpressure all return typed outcomes.
- **Deterministic chunking** that partitions the source exactly, and
  versioned facets (lengths, language, URLs, identifier patterns).
- **Classification** by source-profile rules. Chunks no rule types are
  `pending{no_classifier}` until a classifier binding exists.
- **Delivery** through a transactional outbox to JSONL, with per-sink
  cursors.
- An embedded **SQLite** operational store whose ledger rows cannot be
  deleted.
- An authenticated **HTTP sidecar** (`core-memory serve`) with per-token
  scope grants, readiness, background delivery and an OpenAPI document.

Not built yet: model classification, relationship judging, the memoriesQL
sink and governed deletion.

## Workspace

| Crate | Role |
|---|---|
| `core-memory-contract` | SourceEvent, outcomes and the `core_memory.record.v1` envelope |
| `core-memory-chunker` | Deterministic, lossless text chunking |
| `core-memory-core` | The pipeline: admission, facets, classification, delivery |
| `core-memory-store-sqlite` | Default operational store |
| `core-memory-source-jsonl`, `core-memory-sink-jsonl` | JSON Lines adapters |
| `core-memory-sidecar` | The `core-memory` command: `ingest`, `serve`, `deliver`, `check`, `token`, `schema`, `openapi` |
| `core-memory` | Facade re-exporting the above |

## Looking for Core Memory 1.x?

The Python implementation lives on the
[`v1.x`](https://github.com/JohnnyFiv3r/Core-Memory/tree/v1.x) branch and stays
on PyPI as the maintenance line:

```bash
pip install "core-memory<2"
```

Releases up to 1.1.2 are tagged on `v1.x`. Bug and security fixes for 1.x are
made there.

## License

Apache-2.0. See [LICENSE](LICENSE).
