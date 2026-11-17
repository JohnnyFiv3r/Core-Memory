<p align="center">
  <img src="docs/assets/core-memory-hero-banner.jpg" alt="Core Memory banner" />
</p>

# Core Memory 2.0 (in development)

Core Memory 2.0 is a ground-up rewrite in Rust. It turns any stream of source
events (messages, documents, record changes, an agent's tool calls) into
durable, auditable memory in PostgreSQL. For each event it:

1. admits the event with its exact bytes and a stable identity;
2. cuts it into deterministic chunks and attaches deterministic facets;
3. commits the event, its chunks and their text in one transaction;
4. if you configure a model, has it read each chunk: its type, and where
   its entities, actions and outcomes are;
5. if you configure a model for tool calls, has it say of each call what it
   did and what came of it.

The full chunk text is the memory. The engine never writes summaries or other
generated text in its place. What a model reads in a chunk is stored as a type
from your vocabulary and as ranges of the chunk's own text. A tool call is the
one exception: beside its full text, the engine keeps two short lines a model
wrote about it, because a later reader is better served by those than by
thousands of lines of output.

Relationship judging is designed and not built yet.
[docs/architecture.md](docs/architecture.md) describes the design, including
the planned parts.

This branch is pre-release. Nothing from it is published to PyPI or crates.io.

## Try it

You need Rust 1.89 or newer and PostgreSQL 15 or newer. To start a local
database with Docker:

```bash
docker run -d --name core-memory-pg -e POSTGRES_PASSWORD=secret \
    -p 127.0.0.1:5432:5432 postgres:18
export CORE_MEMORY_DATABASE_URL=postgresql://postgres:secret@127.0.0.1:5432/postgres
```

Then build the `core-memory` command and run the example:

```bash
cargo build --release
export PATH="$PWD/target/release:$PATH"
cd examples/quickstart

core-memory migrate                            # create the core_memory schema
core-memory scope add acme --workspace demo    # allow events for scope "acme"
core-memory ingest --input events.jsonl        # one outcome per input line
core-memory export                             # what is stored, as JSON Lines
```

`ingest` prints one typed outcome per line: `accepted`, `replay`,
`identity_conflict`, `checkpoint_conflict`, `refused` or `busy`. Run it again
and every line comes back as `replay`, with nothing new stored. It stops at
the first event the database cannot take, so that no later event is stored
ahead of it; running it again resumes there.

`core-memory schema record` prints the record contract as JSON Schema.

## Workspaces and scopes

A **scope** is one connection to one source, such as one agent harness or one
Slack workspace. Events are admitted only for a scope that is enrolled and
active.

A **workspace** is a set of scopes that belong together. Each workspace has a
secret key that event and chunk IDs are derived from, so IDs are stable
across replays and reveal nothing about the event they name.

```bash
core-memory scope list
core-memory scope pause acme     # refuse events until resumed
core-memory scope resume acme
core-memory scope revoke acme    # withdraw; `scope add` reconnects it
```

A change of state applies to the next event: the engine reads the scope's
state inside the transaction that stores the event.

## Over HTTP

`core-memory serve` runs the engine as a service. Generate a token for each
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
| `POST /v1/events` | Admit one SourceEvent. The body is the typed outcome. An accepted event is committed before the answer is sent. |
| `POST /v1/events/batch` | `{"events": [...]}`. One outcome per event, in order. Read each one. |
| `GET /v1/checkpoints?scope=&key=` | A reader's stored position. |
| `GET /v1/ready` | 503 with the kind of problem: `store_unavailable`, `store_refused` (the database will not let the engine in as it is configured) or `schema_mismatch`. |
| `GET /v1/health` | 200 while the process is up. |
| `GET /v1/openapi.json` | OpenAPI 3.1, generated from the contract types. |

| Outcome | Status |
|---|---|
| `accepted`, `replay` | 200 |
| `refused: malformed` | 400 |
| `refused: scope_denied`, `refused: scope_not_active` | 403 |
| `identity_conflict`, `checkpoint_conflict` | 409 |
| `refused: tombstoned` | 410 |
| `refused: oversize` | 413 |
| `refused: invalid_profile` | 422 |
| `busy` | 503 with `Retry-After` |

`busy` means the event was not stored and the request is not at fault: keep
the event and send it again. Sending an event again is always safe: an event
that was stored comes back as `replay`. Most often the database was busy or
out of reach for a moment. It is also the answer while the database refuses
the engine's login or is at another schema version, which someone has to
fix: `/v1/ready` and the log say which.

An event that could be read two ways is refused as `malformed`: a duplicate
key at any depth, an unknown field inside a known one such as `actor` or
`structure`, or an integer too large to survive canonical JSON. So is JSON
nested more than 64 levels deep. Unknown top-level fields are kept. A
refusal lists at most 32 problems and says how many more there were.

The service speaks plain HTTP. Keep it on the loopback interface, or put a
proxy that terminates TLS in front of it. It checks the token before it
reads any of the body, and these settings bound what a connection may do:

```toml
[http]
max_body_bytes = 16777216     # larger bodies are refused
max_batch_events = 256
read_timeout_ms = 30000       # to send headers or a body; also how long an idle connection is kept
write_timeout_ms = 30000      # for a client to read an answer before its connection is closed
max_connections = 1024
shutdown_grace_ms = 10000     # for requests in flight when the service is stopped
```

A caller may close its side of the connection once it has sent a request;
the request is still answered. An answer sent before the request's body was
read, such as a 401, closes the connection.

A connection is kept open for further requests only while they carry a
valid token. The answer to a request without one, `/v1/health` and
`/v1/ready` included, ends its connection.

A client is taken to have stopped reading when a response makes no progress
for `write_timeout_ms`. Progress shows in steps of up to about 64 kB, so a
client must read at least that much of a long answer in each such period:
about 2 kB a second with the default.

## Checkpoints

A reader that tails a source can store its position with the event it just
read. Add a `checkpoint` to the event:

```json
"checkpoint": {"key": "session-1", "expected_sequence": 4, "next_sequence": 5, "hash": "<sha256 of the reader's position>"}
```

The position advances in the same transaction as the event, and only from
`expected_sequence`. If another reader moved it first, the outcome is
`checkpoint_conflict` with the stored sequence, and the event is not stored.
After a restart the reader asks `GET /v1/checkpoints` where to resume.

## Reading with a model

The engine calls no model until you configure one. Add a reader and the
binding it uses:

```toml
[reader]
binding = "small"

[[binding]]
id = "small"
provider = "anthropic"                # or "openai", or "openai-compatible" for a local server
endpoint = "https://api.anthropic.com"
model = "<a pinned model snapshot>"
max_output_tokens = 4096
api_key_env = "ANTHROPIC_API_KEY"     # the variable that holds the key; never the key itself
budget = { max_requests_per_day = 2000, max_tokens_per_day = 5000000 }
```

A scope's text is sent to a model only after that scope allows the model's
destination, which is the host of the binding's endpoint:

```bash
core-memory check                                 # prints each binding and its destination
core-memory scope allow acme --binding small      # consent, for this scope and this destination
core-memory read run                              # read what is due now, then stop
core-memory read status                           # queued, being read, failed
core-memory scope disallow acme --binding small   # no later call is permitted
```

`core-memory serve` reads in the background while it serves. Chunks admitted
while a reader is configured are queued by themselves. For what was stored
before, run `core-memory read queue <scope>`.

For each chunk the model picks one type from your vocabulary, or abstains and
says why, and quotes the chunk's entities, actions and outcomes. A quote is
kept only as the byte range where it was found in the chunk, so nothing the
model wrote is stored and a quote that is not in the chunk is dropped. The
result is exported as a `chunk_read` record.

Every call is permitted first, in the transaction that reads the text: the
scope must be active, it must have allowed the destination, and the day's
budget must not be spent. The permit writes a receipt that names the
destination and the chunks sent, and holds no text. Calls that fail are tried
again; after `max_attempts` a chunk is set aside until `core-memory read
retry`. No call is made more often than that for one chunk, whatever fails,
and a chunk the model declines when it is sent alone is set aside at once.
Logs and receipts never hold chunk text or what a model said.

### Tool calls

An agent's call of a tool is an event like any other, with the call as its
content:

```json
{"scope": "acme", "source_ns": "agent/session-7f3a", "external_id": "toolu_01HqPz",
 "profile_id": "core.tool_call", "media_type": "application/json",
 "content": {"tool_call": {"tool": "Bash", "input": {"command": "cargo test"},
                           "output": "running 51 tests\n...", "is_error": true}},
 "parser_contract_version": "agent-log/1"}
```

Its text is stored whole: the arguments, then the output. To have a model
read tool calls, add a tool reader. It may use the same binding as the
reader of chunks or another one:

```toml
[tool_reader]
binding = "small"
```

The engine first shortens the call's text by fixed rules: repeated lines
become one line and a count, the middle of a long output is left out except
for lines that report trouble, and very long lines keep their start and
end. A line that reports trouble is never counted in with lines that do
not, and what is shown is never longer than what is stored. The model is
shown what is left and writes an action of at most 120
characters, an outcome of at most 200, and whether the call succeeded,
failed, partly succeeded or cannot be told. That is exported as a
`tool_outcome` record, and it is erased with the call it is about.

Consent, receipts, budgets and retries work as they do for chunks.
`read status` and `read queue` print one line for chunks and one for tool
calls.

## Erasing

```bash
core-memory erase --reason user_request event <event_id>
core-memory erase --reason user_request key --scope acme --source-ns docs/drive \
    --external-id cutover-plan.md               # every revision of that object
core-memory erase --reason user_request key --scope acme --source-ns docs/drive \
    --external-id cutover-plan.md --revision v3  # that revision only
core-memory erase --reason account_closed scope acme
```

Erasing removes a whole message: its text, its facets, its key, its hashes,
its metadata and what a model read in it or wrote about it, in one
transaction. Its IDs stay as
a tombstone, so links to it do not dangle and the same message is refused if
it is offered again. Each erasure is written to a journal with the reason and
who asked, and exported as an `erasure` record.

`erase scope` works in batches. Pause or revoke the scope first if nothing
more should be admitted to it while it is erased.

PostgreSQL keeps deleted rows on disk until `VACUUM`, erased keys in index
pages until `REINDEX`, and both in WAL and backups until those expire. Vacuum
and reindex after erasing, and treat your backup retention as the longest
that erased text can survive.

`erase key` without `--revision` succeeds when no revision is left to erase,
and prints how many it erased: an erased event keeps no key, so the command
cannot tell an object that was erased before from one that was never stored.

Every command's exit status is 0 when done, 1 on failure, 2 for wrong usage,
3 when no event is stored under the ID given, and 75 when the database was
busy or unreachable and the same command will work later. A database that
refuses the login, has no such database or fails the TLS check is a failure
(1): running the command again would end the same way. So is a result that
could not be written because nothing reads the command's output any more.

## Running inside another system's database

The engine owns one schema, `core_memory`, and can share a database with the
system that uses it. That system:

- **governs** by calling SQL functions inside its own transactions:
  `core_memory.create_workspace`, `enroll_scope`, `set_scope_state`,
  `grant_destination`, `revoke_destination`, `erase_event`, `erase_events`,
  `erase_key` and `erase_scope`. The `core-memory` command calls the same
  functions. They need the default `READ COMMITTED` isolation, and are best
  called before the transaction reads the engine's tables itself.
- **reads** the engine's tables directly. Row security is on for every table
  and nothing is granted to `PUBLIC`, so a role sees rows only after it is
  given a grant and a policy.
- **never writes** the engine's tables.

Two roles are created by the first migration:

| Role | May |
|---|---|
| `core_memory_engine` | Read and insert events, chunks, beads, receipts and reads. It cannot change or delete them, change a scope's state, give model consent, or erase. Give it to the engine's login role. |
| `core_memory_governor` | Call the governance functions. It reads no table. |

Both roles belong to the database cluster. If one cluster holds several Core
Memory databases with different operators, revoke `CONNECT` on each database
from the logins of the others.

Every row in `events`, `beads` and `erasures` has a `seq`. Within a workspace,
rows commit in `seq` order, so a reader that pages by
`workspace_id = $1 AND seq > $2` never misses a row.

## Connecting to the database

The URL is read from `CORE_MEMORY_DATABASE_URL`, or from the variable named
by `[store] url_env`. It is never printed.

A connection to another machine always uses TLS and checks the server's
certificate and host name. For a server whose certificate comes from a
private CA, such as Amazon RDS, set `ca_file`:

```toml
[store]
ca_file = "rds-ca.pem"
pool_size = 4            # connections `serve` keeps
```

`ca_file` may also hold a server's self-signed certificate, as long as that
certificate is not itself marked as a certificate authority. `openssl req
-x509` marks it as one unless it is given `-addext
basicConstraints=critical,CA:FALSE`.

Add `sslmode=disable` to the URL only if the network between the engine and
the database is trusted.

```toml
[store]
connect_timeout_ms = 10000     # to reach the server, for each address its name resolves to
statement_timeout_ms = 30000   # for one statement
```

Opening a connection, from looking up the server's name to a session that
is ready, is given three times `connect_timeout_ms` for each host the URL
names. A server that accepts the connection and then says nothing is given
up on after that, and reported as unreachable.

## Upgrading

Run `core-memory migrate` from the new build, as the database role that owns
the `core_memory` schema, then replace the running engines. An engine writes
only to the schema version it was built for. After the migration, engines
from the old build answer `busy` until they are replaced, so nothing is
written by code that does not know the new schema.

## What works today

- **Admission** with stable identity: `(scope, source_ns, external_id,
  revision)`. Replays, identity conflicts, tombstones, scope grants and
  states, size limits and reader checkpoints all return typed outcomes.
- **Deterministic chunking** that partitions the source exactly, and
  versioned facets (lengths, language, URLs, identifier patterns).
- **One PostgreSQL store** with immutable rows, row security, a least-privilege
  engine role and migrations.
- **Erasure** of whole messages, with a journal.
- **Reading by a model**: each chunk's type and the places of its entities,
  actions and outcomes, behind per-scope consent, a receipt for every call
  and a daily budget. Transports for Anthropic, OpenAI and OpenAI-compatible
  servers.
- **Tool calls**: stored whole as one bead, shortened by fixed rules, and
  read by a model that records what each call did and what came of it.
- **An authenticated HTTP service** with per-token scope grants, readiness and
  an OpenAPI document.
- **Export** of events, chunks, reads, tool outcomes and erasures as
  `core_memory.record.v2` JSON Lines.

Not built yet: relationship judging. Reading has been tested against
stand-in models only, not yet against a live one.

## Workspace

| Crate | Role |
|---|---|
| `core-memory-contract` | SourceEvent, outcomes, IDs and the `core_memory.record.v2` records |
| `core-memory-chunker` | Deterministic, lossless text chunking |
| `core-memory-core` | The pipeline: admission, facets and the `Store` trait |
| `core-memory-store-postgres` | The PostgreSQL store, its schema and migrations |
| `core-memory-source-jsonl` | Reads SourceEvents from JSON Lines |
| `core-memory-model-anthropic`, `core-memory-model-openai` | Model transports (`core-memory-model-http` holds their shared plumbing) |
| `core-memory-sidecar` | The `core-memory` command |
| `core-memory` | Facade re-exporting the above |

## Developing

```bash
cargo fmt --all
cargo +1.99 clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace                 # tests that need no database
python3 scripts/check_layering.py
```

Tests that touch the store run against a real PostgreSQL server. Each test
creates and drops its own database:

```bash
export CORE_MEMORY_TEST_DATABASE_URL=postgresql://postgres:secret@127.0.0.1:5432/postgres
cargo test --workspace --all-features
```

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
