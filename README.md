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

### Delivering to memoriesQL

memoriesQL is the reference system of record. Its schema 55 (engine record
intake) accepts the engine's records exactly as delivered, keeps the first
record under each key in each engine scope, and lets a pending type be decided
once.

In memoriesQL, enroll each engine scope as an exact source
(`source_system = "core-memory"`, `object_kind = "engine_scope"`, the scope as
`external_object_id`), pair the engine as a `background_service` with the
`engine.records.deliver` capability, and grant it `write` on those sources.
memoriesQL shows the stored record in a conflict only to a reader, so to see
it, also give the engine `source.read` and a `read` grant. Then:

```toml
[[sink]]
id = "memoriesql"
kind = "memoriesql"
url_env = "MEMORIESQL_DATABASE_URL"         # postgresql://... ; TLS when the server offers it
secret_env = "MEMORIESQL_ENGINE_CREDENTIAL" # the paired-client secret
workspace_id = "<memoriesQL workspace UUID>"
```

memoriesQL decides what the engine may deliver: types must be its governed
bead types, so load them into the engine's vocabulary with

```bash
core-memory vocabulary --sink memoriesql    # prints a [vocabulary] block
```

A record memoriesQL cannot accept yet (an engine scope it has not enrolled or
granted) is retried; one it refuses (an ungoverned type, a mismatched hash) is
recorded as a delivery failure. The sink runs the conformance suite against a
real memoriesQL database in CI.

### Delivering to your own service

A `webhook` sink posts records to a URL you run, in batches, signed with a
shared secret:

```toml
[[sink]]
id = "host"
kind = "webhook"
url = "https://memory.internal.example.com/core-memory"
secret_env = "CORE_MEMORY_WEBHOOK_SECRET"   # at least 16 bytes
timeout_ms = 10000
```

Each request body is `{"contract": "core_memory.record.v1", "sink": "host",
"records": [...]}`, with the records exactly as the engine wrote them. The
`Core-Memory-Signature: t=<unix seconds>,v1=<hex>` header is HMAC-SHA256 of
`<t>.<body>` under the secret. Check it, refuse old timestamps, and
deduplicate by `record_key`, because delivery is at least once.

Answer `204` (or any `2xx`) to accept the whole batch, or `200` with
`{"results": [...]}`, one `{"outcome": ...}` per record (`accepted`,
`already_accepted`, `conflict`, `rejected`, `retryable`). Any other status
means nothing was written; the batch is sent again later, after
`Retry-After` if you give one. The `core-memory-sink-webhook` crate
documents the protocol and exports `verify` for Rust receivers.

### Model classification

Chunks that no profile rule types can be classified by a model. Add a
classifier and the binding it uses:

```toml
[classifier]
binding = "claude"
batch_size = 8        # chunks per call
deadline_ms = 10000   # hold a record this long for its type, then deliver it as pending

[[binding]]
id = "claude"
provider = "anthropic"
endpoint = "https://api.anthropic.com"
model = "claude-opus-5-5"
reasoning_effort = "low"   # classification rarely needs more; raise it if quality needs it
max_output_tokens = 4096
scopes = ["acme"]          # whose content may be sent to this provider
budget = { max_requests_per_day = 5000, max_tokens_per_day = 20000000 }
```

The key comes from `ANTHROPIC_API_KEY` (or `api_key_env`). For OpenAI use
`provider = "openai"` (`OPENAI_API_KEY`); for Ollama, vLLM, OpenRouter or LM
Studio use `provider = "openai-compatible"` with the server's `/v1` URL as the
endpoint.

How it behaves:

- The model sees chunks under handles `C1..Cn` and must answer each exactly
  once with a listed type or an abstention (`no_fit`, `insufficient_evidence`,
  `ambiguous`). Any other reply is rejected whole and retried once with the
  validator's errors. No model text ever becomes a label.
- A chunk's record waits for its type up to `deadline_ms`. If the model has
  not decided by then, the record is delivered as `pending` with the reason,
  and the decision follows as a `type_decision` record.
- Every call is charged to the binding's daily budget for that scope before it
  is sent, and recorded in an attempt journal. A spent budget waits for the
  next UTC day.
- The engine refuses to start if the classifier's binding has no credential or
  uses a setting its provider rejects (for example `temperature` on Anthropic).
  It never silently runs without the classifier it was configured with.
- Decisions record the binding and its revision, a hash of everything that
  affects decisions (model, endpoint, effort, prompt template, validator).

`core-memory ingest` classifies before it delivers, and releases records still
waiting as pending when it ends. `core-memory classify` retries pending
decisions later. `core-memory serve` classifies in the background.

## What works today

- **Admission** with stable identity: `(scope, source_ns, external_id,
  revision)`. Replays, identity conflicts, tombstones, scope grants, size
  limits and backpressure all return typed outcomes.
- **Deterministic chunking** that partitions the source exactly, and
  versioned facets (lengths, language, URLs, identifier patterns).
- **Classification** by source-profile rules, then a model binding
  (Anthropic or OpenAI-compatible) in closed-world batches, with budgets and
  an attempt journal. Without a classifier, chunks no rule types are
  `pending{no_classifier}`.
- **Delivery** through a transactional outbox to memoriesQL, JSONL files or
  signed webhooks, with per-sink cursors. Every sink adapter must pass the same conformance suite
  (`core-memory-conformance`), which checks what the sink holds against
  what the engine sent.
- An embedded **SQLite** operational store whose ledger rows cannot be
  deleted.
- An authenticated **HTTP sidecar** (`core-memory serve`) with per-token
  scope grants, readiness, background delivery and an OpenAPI document.

Not built yet: relationship judging and governed deletion.

## Workspace

| Crate | Role |
|---|---|
| `core-memory-contract` | SourceEvent, outcomes and the `core_memory.record.v1` envelope |
| `core-memory-chunker` | Deterministic, lossless text chunking |
| `core-memory-core` | The pipeline: admission, facets, classification, delivery |
| `core-memory-store-sqlite` | Default operational store |
| `core-memory-source-jsonl`, `core-memory-sink-jsonl` | JSON Lines adapters |
| `core-memory-sink-webhook` | Signed HTTP delivery to a service you run |
| `core-memory-sink-memoriesql` | Delivery to memoriesQL, the system of record |
| `core-memory-model-anthropic`, `core-memory-model-openai` | Model transports (`core-memory-model-http` holds their shared plumbing) |
| `core-memory-sidecar` | The `core-memory` command: `ingest`, `serve`, `classify`, `deliver`, `check`, `token`, `schema`, `openapi`, `vocabulary` |
| `core-memory` | Facade re-exporting the above |
| `core-memory-conformance` | The conformance suite every sink adapter runs, with golden records (test-only) |

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
