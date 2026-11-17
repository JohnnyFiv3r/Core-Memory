# Core Memory 2.0 architecture

This document describes how the engine is built and why. `CLAUDE.md` holds
the invariants; this file holds the design behind them. Sections marked
*planned* describe work that is designed but not merged yet.

## What the engine does

For each SourceEvent the engine:

1. admits it with its exact bytes and a stable identity;
2. cuts it into deterministic chunks and computes deterministic facets;
3. writes the event, its chunks and its beads to PostgreSQL in one
   transaction;
4. asks a model for each chunk's type and where its entities, actions and
   outcomes are, if a reader is configured and the chunk's scope allows it;
5. *planned:* compresses tool calls and asks a model for each one's action
   and outcome;
6. *planned:* asks a judge model how new beads relate to recent ones.

The engine has no retrieval API. Consumers read its tables or its export.

## One store: PostgreSQL

PostgreSQL is the only durable store, and it is the system of record. There
is no outbox and no delivery step: what admission commits is the record.

The engine owns the `core_memory` schema and its migrations
(`crates/core-memory-store-postgres/sql`). `core-memory migrate` applies
them. An engine reads and writes only the schema version it was built for
(see [Upgrading](#upgrading)).

PostgreSQL 15 or newer is required. CI runs the tests on 15 and 18.

A host such as memoriesQL runs the engine against its own database. The
engine's tables then sit beside the host's, so a host transaction can erase
or revoke in the engine atomically. The dependency points one way: the host
depends on the engine's schema, and the engine knows nothing about the host.

Three rules keep ownership clear:

- **Only the engine writes data tables.** Other writers go through the
  engine's API.
- **Governance is a set of SQL functions.** Enrolling, pausing and revoking a
  scope, granting a model destination, and erasing are functions in the
  `core_memory` schema. A host calls them inside its own transactions; the
  `core-memory` command calls the same functions. They need a transaction
  at `READ COMMITTED`, the PostgreSQL default, and refuse `REPEATABLE READ`
  and `SERIALIZABLE` before they read anything. A host transaction should
  call them before it reads `core_memory` tables itself (see
  [Upgrading](#upgrading)).
- **Hosts read through their own grants.** Row security is on for every
  table and nothing is granted to `PUBLIC`, so a role sees rows only where
  a policy names it.

## Vocabulary

| Term | Meaning |
|---|---|
| Workspace | A set of scopes whose beads may be judged together. Derived IDs are keyed by a secret that belongs to the workspace. |
| Scope | One connection to one source, such as one agent harness. Capture, model consent, pausing and revocation are per scope. A scope belongs to one workspace for good. |
| Event | One SourceEvent: one message, record change, document or tool call. Identified by `(scope, source_ns, external_id, revision)`. |
| Chunk | A byte range of an event's content. Chunks partition the content exactly. |
| Bead | An unchanging pointer that other things link to. Every chunk of a message is a bead. *Planned:* tool calls, labels and groups are beads too. |
| Checkpoint | A reader's durable position in its source, advanced in the same transaction as an admission. |
| Binding | A pinned model configuration: provider, endpoint, model snapshot and settings. |
| Destination | The host, and port if any, of a binding's endpoint. Model consent is given per scope and per destination. |

## Tables

All tables are in the `core_memory` schema.

| Table | Holds | On erase |
|---|---|---|
| `workspaces` | The workspace ID and its ID key. | Not erased. |
| `workspace_seq` | The workspace's change counter and write lock (see [Change order](#change-order)). | Not erased. |
| `scopes` | Scope, workspace and state (`active`, `paused`, `revoked`). | Not erased. |
| `events` | Identity, hashes, source metadata (`info`), profile and chunker revisions, ordering time, `seq`. | Key fields, hashes and `info` are cleared. The row and its ID stay. |
| `chunks` | Chunk ID, event, ordinal and ranges. No text. | Stays. |
| `chunk_text` | The chunk's text, its hash and its facets. This is the only copy of source text. | Deleted. |
| `beads` | Bead ID, kind, source pointer, ordering time, state, `seq`. | Marked `erased`. The row and its ID stay. |
| `checkpoints` | A reader's sequence and hash per `(scope, key)`. | Not erased. |
| `pins` | The hash of each profile and chunker revision ever used. | Not erased. |
| `erasures` | The journal of erasures: IDs, reason, actor, time, `seq`. | Kept. Replayed after a backup restore. |
| `scope_destinations` | Where a scope's text may be sent: destination and whether it is granted or revoked. | Not erased. |
| `outbound_calls` | One receipt for each model call: destination, binding, the chunk IDs sent, byte and token counts, `seq`. No text. | Kept. |
| `outbound_results` | How each call ended, and the tokens it used. | Kept. |
| `chunk_reads` | What a model read in a chunk: a type of the vocabulary or why none, the reader that read it, `seq`. | Deleted. |
| `chunk_read_spans` | Where a read found an entity, an action or an outcome: byte ranges of the chunk's text. | Deleted. |
| `work` | Chunks still to be read: IDs, attempts, when to try next and a problem code. No text. | Deleted. |

*Planned* tables: `tool_outcomes`, `edges`, `verdicts`, `name_links`.

Text is kept in one place on purpose. Erasing a message deletes its
`chunk_text` rows and every row derived from that text, in one transaction.
Tables that survive an erase hold IDs, ranges and engine-side times only.

Rows are immutable. Triggers refuse updates and deletes on `events`,
`chunks`, `chunk_text`, `beads`, `pins`, `erasures`, `outbound_calls`,
`outbound_results`, `chunk_reads` and `chunk_read_spans`, and refuse
`TRUNCATE` on every table above. The one exception is an erase, and the triggers
allow exactly what an erase does: an event or a bead goes from live to
erased and keeps its engine-side columns, and text, reads and spans are
deleted only for an event that was just erased. A scope keeps its name and
workspace. The triggers stop mistakes, including the table owner's. They
are not a defence against the owner, who can drop them.

`work` is the one table the engine changes in place. It is a queue, not a
record: a row says what is still owed and is deleted when it is done.

## Identity

`event_key = (scope, source_ns, external_id, revision)`.

`event_id` and `chunk_id` are UUIDs derived from the event key with
HMAC-SHA-256 under the workspace's ID key. IDs are deterministic, so a
replay finds the same row. They are keyed, so an ID that survives an erase
does not let anyone confirm a guessed key.

The identity check compares the event-level hash before chunking:

| Stored | Offered | Outcome |
|---|---|---|
| Nothing | Any | Admitted. |
| Live, same hash | | `replay`. Nothing is written. |
| Live, different hash | Profile not editable, or a revision was given | `identity_conflict`. |
| Live, different hash | Editable profile, no revision | Admitted as a new revision, named from the content. |
| Erased | Any | `refused: tombstoned`. The surviving `event_id` is the tombstone, so no hash of the erased message is kept. |

An editable record's later versions are stored as revisions of its first
version, and only while that first version is live. Two first versions that
arrive together are both stored: one as the record, the other as a revision
of it.

A revision the engine names is named from the event's hash, so that the
same edit offered again is a replay. The ID of such a revision is therefore
a keyed hash of the record's key and content. It confirms nothing to anyone
without the workspace's ID key.

The engine refuses input that could be read two ways, as `malformed`: a
duplicate key at any depth, an unknown field inside a known one, and an
integer in an unknown field or in JSON content that canonical JSON would
round. It also refuses JSON nested more than 64 levels deep, counted from
the event. Parsers stop at some depth, and what is accepted must read back
from inside the records that later carry it.

A refusal lists at most 32 problems, the last of which counts the rest, and
each is cut to a bounded length. An answer does not grow with what it
refuses.

## Admission is one transaction

`Engine::admit` validates and chunks first, with no transaction open. Then
the store commits everything at once, starting with
`core_memory.begin_write`:

1. Hold the schema: wait for a migration that is running, and keep the
   next one from starting until this transaction ends.
2. Check that the store is at the engine's schema version.
3. Lock the workspace's change counter. The transaction is now the
   workspace's only writer until it ends.
4. Read the scope's state under that lock and require `active`. Pausing,
   revoking and erasing take the same lock, so none of them interleaves
   with a write.
5. Take the event's and its beads' numbers from the counter.
6. If the event is an edit of an editable record, require that record to
   be live.
7. Insert the event. If the ID already exists, write nothing and report
   what is stored.
8. Insert its chunks, their text and facets, and its beads.
9. Advance the reader's checkpoint, if one was offered.

One lock, taken first by every writer, is what keeps writers of a workspace
from deadlocking with each other, in whatever order a host combines
governance calls in one transaction. A transaction that governs several
workspaces should take them in a consistent order.

Callers authenticate to the engine with bearer tokens that carry scope
grants. The scope's own state is read from the database inside the
transaction, so revocation takes effect at once.

Content that contains U+0000 is refused, because PostgreSQL text cannot hold
it and the engine never alters content.

An event's content is bounded by its profile's `max_event_bytes`, and its
metadata (everything else it declares, as JSON) by
`limits.max_metadata_bytes`. Over either, the event is refused as
`oversize`.

## Ordering

Each event and bead has `time_at` and `time_basis`:

| Basis | When | `time_at` |
|---|---|---|
| `source` | The source gave an instant, to the second or better. | That instant. |
| `source_bucket` | The source gave a coarser time, such as a day. | The start of that period, in the offset the source gave. Events of one period are then ordered by admission. |
| `captured` | The source gave no usable time. | When the engine admitted the event. |

Events and beads are ordered in time by `(time_at, seq)`.

## Change order

Each workspace has a change counter. Every row the engine writes for the
workspace in `events`, `beads`, `erasures`, `outbound_calls` and
`chunk_reads` carries `seq`, the next number from that counter. Taking a number locks the counter until the transaction
ends, so:

- within a workspace, rows commit in `seq` order;
- a reader that pages a table by `workspace_id = W AND seq > last seen`
  never misses a row, with no extra rule to follow;
- writers of one workspace commit one at a time: admissions, changes of
  scope state and erasures alike. Writers of different workspaces do not
  wait for each other.

A transaction that takes numbers and then writes nothing (a duplicate
admission, a checkpoint conflict) rolls back and returns them.

Numbers come from one counter per workspace, so a `seq` identifies one row
across those tables. An erasure changes existing rows in place and adds
one row to `erasures`; a reader that keeps derived data follows `erasures`
to learn what to remove.

Sequences owned by PostgreSQL would not give this guarantee: they hand out
numbers before commit, so a higher number can become visible before a lower
one.

## Checkpoints

A reader may attach a checkpoint to an event: a key, the sequence it expects
the store to hold, the next sequence, and a hash of its own position.

| Stored sequence | Result |
|---|---|
| Equals `expected_sequence` | Advanced to `next_sequence`. |
| Equals `next_sequence`, same hash | Already advanced. The same receipt is returned, so a retry after a crash is safe. |
| Anything else | `checkpoint_conflict`, with what is stored. Nothing is written. |

A checkpoint advances only with an `accepted` or `replay` outcome. A reader
that restarts asks the engine for its checkpoint and resumes from there.

## Erasing

`core_memory.erase_event(event_id, reason, actor)` erases one whole message.
In one transaction it deletes the event's text and facets, what a model
read in it and the work still queued for it, clears the event's key fields,
hashes and metadata, marks its beads erased, and writes the journal. IDs,
ranges and links stay as the tombstone. Offering the event again is
refused, and so is any later row about it: the table that holds what a
model read takes rows only for an event that is live.

`core_memory.erase_events(event_ids, reason, actor)` erases a list of
messages in one call and returns one row for each distinct ID: `erased`,
`already_erased` or `unknown`. It takes each workspace once, however many of
its events it erases, so it is the way to erase more than a handful.
Thousands of `erase_event` calls in one transaction hold the workspace for
as long as they take, and each is slower than the one before.

`core_memory.erase_key(scope, source_ns, external_id, reason, actor)` erases
every live revision of one source object.

`core_memory.erase_scope(scope, reason, actor, max_events)` erases live
events of a scope, oldest first: all of them, or at most `max_events`. It
holds the workspace while it runs, so no event is admitted to the scope
halfway through. To erase a large scope without holding its workspace for
long, stop capture first by pausing or revoking the scope, then call it in
batches until it returns 0. `core-memory erase scope` does this.

Erasing is a function, not an HTTP call, so a host can make it part of its
own governed deletion. The engine decides nothing about who may erase.

PostgreSQL keeps what was erased in places the erase cannot reach:

- **Dead tuples**, until `VACUUM`.
- **Index pages.** `events_key_idx` can keep an erased key as a page
  boundary after `VACUUM`. `REINDEX INDEX core_memory.events_key_idx`
  removes it.
- **WAL and backups**, until they expire.

Operators should vacuum and reindex after erasing, and treat their backup
retention as the longest erased text can survive.

The planner's statistics are not on this list. `ANALYZE` copies sample
values of a column into `pg_statistic`, so the columns that hold what a
source said, or a hash of it, are excluded from statistics. That includes
`time_at` on events and beads, which is the source's own time until the
event is erased, and a checkpoint's hash.

The planner therefore has no histogram for `time_at`. A query for one
workspace's beads in a range of time uses `beads_window_idx` without one,
and so does `ORDER BY time_at, seq` with a limit. A host that filters on
`time_at` across workspaces should expect a default estimate.

## Roles and row security

| Role | May |
|---|---|
| `core_memory_engine` | Read and write the data tables. It reads model consent and cannot give it. The engine's login role is a member. |
| `core_memory_governor` | Execute the governance functions, which include giving and withdrawing model consent. Holds no table privileges. |

Both are created by the first migration if they do not exist. Every table
has row security enabled, with a policy for `core_memory_engine` only. A
host that reads beads or text adds policies for its own roles.

The two roles belong to the database cluster, not to one database. A login
that holds one of them holds it in every Core Memory database of the
cluster it can connect to. Where one cluster holds databases that should
not share operators, revoke `CONNECT` on each from the logins of the
others.

Every function in the schema runs with `search_path` set to
`pg_catalog, pg_temp`, and every engine session sets the same. Nothing a
role creates in `public` or in a temporary schema is ever picked up in
place of a built-in function or type.

## Connecting

The database URL may carry a password, so it is read from an environment
variable by default and is never printed.

A connection to another machine always uses TLS, unless the URL says
`sslmode=disable`. The server's certificate and host name are checked
against the public web roots plus an optional private CA (`ca_file`). Only a
connection to the same machine may fall back to plain text when the server
offers no TLS.

Each connection sets a statement timeout, a 5-second lock timeout and a
15-second limit on idling inside a transaction, so a stalled engine cannot
hold a workspace's writers for long. It also sets its search path and
`READ COMMITTED` isolation, whatever the database's defaults are.

The driver's connect timeout covers reaching the server and not what the
server says next, so each attempt to connect runs on a thread of its own
and its caller waits no longer than three times the connect timeout for
each host named. An attempt that is given up on cannot be ended; it ends
when the server answers or the connection dies, and its connection is then
closed. At most eight such attempts are left running. Past that, connecting
fails at once until one of them ends.

A store call ends in one of these ways when it does not succeed:

- **Unavailable.** A timeout, a lost connection, a server that cannot be
  reached, or a database that takes no writes (a standby after a failover).
  The same request may work later. A caller is answered `busy`, and a retry
  of an event that did commit is a replay. A command exits with 75.
- **Access refused.** The server answered and would not open a session: it
  refused the login, has no such database, or failed the TLS check. Nothing
  will change until the configuration or the server does. A command exits
  with 1. A caller is still answered `busy`, because its request is not at
  fault and it should keep its event, while `/v1/ready` reports
  `store_refused` and the log says why.
- **Schema mismatch.** Treated the same way: `busy` to callers,
  `schema_mismatch` on `/v1/ready`, and 1 from a command.
- **Anything else** would repeat and is the engine's or the store's own
  failure. It is logged with its SQLSTATE and answered as an internal
  error.

## Upgrading

An engine writes only to the schema version it was built for.

1. `core-memory migrate`, run from the new build, applies the pending
   migrations. Each one first locks `schema_migrations`. Writes in flight
   finish; new writes wait.
2. When the migration commits, engines built for the old version are
   refused: every write transaction checks the version inside the database,
   and so does the read that starts every admission. They answer `busy`
   and report `schema_mismatch` on `/v1/ready`.
3. Replace the engines with the new build.

The lock on `schema_migrations` is the first lock everything takes. A call
that arrives during a migration then waits for it holding nothing the
migration needs, so the two cannot deadlock, and what the call reads
afterwards is what the migration left:

- a write starts with `begin_write`, which takes the lock and checks the
  version;
- every governance function takes it before it reads a table;
- a read of several statements, such as an export, takes it before its
  first query. Its snapshot is then taken after any migration it waited
  for, and no migration starts while it runs. A snapshot taken before a
  migration would show a table the migration rewrote as empty;
- a read of one statement has `schema_migrations` first in its FROM
  clause. PostgreSQL locks the tables of a FROM clause in the order they
  are written there; a table named only in the select list is locked after
  them. If such a read fails in a way that would repeat, the store reads
  the version by itself before it reports the failure, so a statement that
  a migration left unable to run is answered as `schema_mismatch` too.

A host transaction that has already read `core_memory` tables and then
calls a governance function can still deadlock with a migration.
PostgreSQL ends one of the two. A migration that was ended this way
changed nothing and can be run again.

A host transaction that changes more than one workspace should take them
in workspace ID order, which is the order `erase_events` takes its own.
Two transactions that take the same two workspaces in opposite orders can
deadlock, and PostgreSQL ends one of them.

Migrate as the role that is to own the schema. A `core_memory` schema that
another role created is refused, because its owner could attach code that
would run as the migrating role.

A migration never changes once released. The runner records a checksum of
each one it applies and refuses a database whose history differs.

Until 2.0.0 is released, the first migration still changes as the engine is
built. A database created by a pre-release build must be recreated, not
migrated.

## Model calls

A model reads each chunk once a reader is configured: a `[reader]` section
that names a `[[binding]]`. Without one the engine calls no model, and
everything else works the same.

### What a read is

The model is shown a batch of chunks, all of one scope and one source
profile, under handles (`C1`, `C2`, ...), with the types it may choose
from. For each chunk it answers with:

- **a type**: one key of the vocabulary, or an abstention (`no_fit`,
  `insufficient_evidence`, `ambiguous`). An abstention is a completed
  judgment. It is stored and is not retried;
- **entities, actions and outcomes**, each as a quote of the chunk.

A quote is looked up in the chunk and kept as the byte range where it was
found. Nothing the model wrote is stored: a type is a key of the
vocabulary, and a span is a range of text the source wrote. A quote that is
not in the chunk is not a finding.

A reply that breaks the shape is rejected whole and repaired once: the
model gets its reply back with what was wrong. On that second attempt a
quote that is still not found is dropped and counted (`spans_rejected`)
rather than failing the batch. A source profile's `types` narrows which
types its chunks may be given.

Every read records its reader: the binding's ID and model, the prompt
revision, and `binding_rev`, a hash of everything that affects what the
model says (provider, endpoint, model, sampling settings, output limit, the
prompt, the reply shape and the validator), plus the vocabulary's hash.
Timeouts, budgets and credentials are not part of it. Reads are added,
never changed: the read with the highest `seq` for a chunk is the current
one.

### The four steps of a call

1. **Permit.** One transaction, starting with `begin_write`, checks that
   the scope is active and has granted the binding's destination, checks
   the budget, reads the text, and writes the call's receipt in
   `outbound_calls`. A refusal sends nothing and writes no receipt. The
   receipt is committed before any text leaves the engine.
2. **Call** the model through `ModelTransport`: one request, with no hidden
   retry or fallback.
3. **Validate** the reply against the closed shape above.
4. **Finish.** One transaction records how the call ended in
   `outbound_results`, stores the reads and their spans, and settles the
   queue. A read is stored only for a chunk the receipt names whose text
   still exists and whose scope is still active. The table itself takes a
   read only for an event that is live, whoever writes it.

   The end of a call is recorded once. A store that is busy when a reply
   arrives, because another transaction holds the workspace or the
   connection dropped, is asked again before the reply is given up, and
   asking again stores nothing twice. A provider's own count of the tokens
   it used is a number from outside: one too large to store is recorded as
   the largest that can be.

A call with a receipt and no result was permitted and never accounted for:
its text may have been sent. A revocation or a pause that commits before a
permit is always seen by it, because both take the workspace's write lock.
One that commits during a call lets the call finish and stores nothing
from it.

A **destination** is the host of a binding's endpoint, in lower case, with
its port if it names one. It is derived from the endpoint and never written
by hand, so a binding pointed somewhere else needs consent again.
`core_memory.grant_destination(scope, destination)` and
`revoke_destination` are governance functions; `core-memory scope allow`
and `scope disallow` call them. The engine's role cannot.

A binding may carry a budget of calls and tokens per workspace per UTC day.
It is counted from the receipts, in tokens reserved: the most a call may
use, whether or not it does.

### The queue

Admission adds one `work` row for each chunk it stores while a reader is
configured. `core-memory read queue <scope>` adds the chunks that were
stored before one was, and with `--reread`, those whose reads were all
made by another binding revision or vocabulary than the configured ones.

A reader claims a batch with `FOR UPDATE SKIP LOCKED`, so readers never
wait for each other, and holds it under a lease. A reader that dies leaves
its batch to be claimed again when the lease runs out.

| What happened | What becomes of the work | `problem` |
|---|---|---|
| The reply was valid | Deleted. The reads are stored. | |
| The scope has not granted the destination | Put off, and due again when it is granted. | `no_consent` |
| The scope is paused or revoked | Put off, and due again when it is active. | `scope_not_active` |
| The day's budget is spent | Put off until the next UTC day. | `over_budget` |
| The provider did not process the request | Tried again after `retry_backoff_ms`, or the provider's own delay. | `provider_unavailable` |
| The request may have been processed | Tried again after `retry_backoff_ms`. | `call_in_doubt` |
| The model declined, or the request or reply did not fit | Tried again alone, one chunk to a call. A chunk that was sent alone is set aside at once: the same request would get the same answer. | `model_refused` |
| The reply was invalid twice | Tried again alone. | `reply_invalid` |
| A chunk of the batch was erased before the repair | Nothing more is sent. The rest is tried again after `retry_backoff_ms`. | `batch_changed` |
| The provider turned the request itself down | Tried again after ten minutes. An operator has to fix the binding. | `binding_rejected` |
| The work was handed out `max_attempts` times and never settled | Set aside, when it would have been handed out again. | `attempts_spent` |

Work that was put off before any call was made does not count as an
attempt. Once a call was made for it, the attempt counts whatever comes
next: a repair that is not permitted leaves the work put off and one attempt
poorer. After `max_attempts` calls a chunk's work is set aside as `failed`,
until `core-memory read retry` puts it back.

The count is also kept where work is handed out. A reader that dies with
its work, or cannot record how its call ended, leaves a lease that runs
out. That work is handed out again, and the call made again, but only
until its attempts are spent. Then it is set aside as `attempts_spent`. No
call is made more than `max_attempts` times for one chunk, whatever fails.
`core-memory read status` shows the counts.

`core-memory serve` runs a reader beside the server when one is
configured, on a thread and a store connection of its own. `core-memory
read run` reads what is due and stops.

### What is never written down

The queue, the receipts, the results and the logs hold IDs, counts and
problem codes. They hold no chunk text and nothing a model or a provider
said: a provider's error message can quote the request.

## Records and export

`core_memory.record.v2` is the public JSON form of the engine's primitives:
`event`, `chunk`, `chunk_read` and `erasure` records today; tool outcomes,
beads, edges and verdicts as they are built. `core-memory export` writes
them as JSON Lines, one workspace after another, each in `seq` order: an
event with its chunks, then what a model read in them as it was read, and
an erasure where an event was erased. An erased event's own records, and
what was read in it, are not exported. Each record carries its `seq`, and
`export --workspace W --after N` resumes after it. The tables are the
truth; records are how they are published outside SQL.

## Crates

```
core-memory-contract -> core-memory-chunker -> core-memory-core
    -> core-memory-store-postgres / core-memory-source-jsonl / core-memory-model-*
    -> core-memory-sidecar, core-memory (facade)
```

`core-memory-core` holds the pipeline and the `Store` trait and imports no
database or provider crate. `scripts/check_layering.py` enforces this.

## Testing

Unit tests need nothing. Tests that touch the store run against a real
PostgreSQL server behind the `postgres-tests` feature:

```bash
CORE_MEMORY_TEST_DATABASE_URL=postgresql://postgres:secret@127.0.0.1:5432/postgres \
    cargo test --workspace --all-features
```

Each test gets its own database, cloned from a migrated template. There is
no in-memory store: the engine's guarantees are transactional, so they are
tested where they hold.
