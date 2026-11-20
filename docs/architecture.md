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
5. shortens each tool call by fixed rules and asks a model what the call
   did and what came of it, if a tool reader is configured and the call's
   scope allows it;
6. asks a judge model how each new bead relates to the beads near it in
   time, across the scopes of its workspace, if a judge is configured and
   each bead's scope allows it.

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
| Bead | An unchanging pointer that other things link to. Every chunk of a message is a bead, and a tool call is one bead whatever its length. These are beads of source content. A judge makes one more kind, which holds no text: a group bead (see [Relationships](#relationships)). |
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
| `beads` | Bead ID, kind (`chunk`, `tool`, `group`), source pointer or the judge's call that made it, ordering time, state, `seq`. | Marked `erased`. The row and its ID stay. |
| `checkpoints` | A reader's sequence and hash per `(scope, key)`. | Not erased. |
| `pins` | The hash of each profile and chunker revision ever used. | Not erased. |
| `erasures` | The journal of erasures: IDs, reason, actor, time, `seq`. | Kept. Replayed after a backup restore. |
| `scope_destinations` | Where a scope's text may be sent: destination and whether it is granted or revoked. | Not erased. |
| `outbound_calls` | One receipt for each model call: destination, binding, the chunk IDs sent and, for a judge's call, every bead shown, byte and token counts, `seq`. No text. | Kept. |
| `outbound_results` | How each call ended, and the tokens it used. | Kept. |
| `chunk_reads` | What a model read in a chunk: a type of the vocabulary or why none, the reader that read it, `seq`. | Deleted. |
| `chunk_read_spans` | Where a read found an entity, an action or an outcome: byte ranges of the chunk's text. | Deleted. |
| `tool_outcomes` | What a model wrote about one tool call: a short action, a short outcome and a status, with the reader and the compressor that produced them, `seq`. | Deleted. |
| `judge_reads` | One row for each read by a judge that was stored: the beads it was shown, by ID, the rubric and binding revisions, `seq`. | Kept. |
| `verdicts` | One row for every pair a judge was shown: the two beads and the scope of each, what it said, the direction, and what code computed about the pair at that moment (`columns`), `seq`. No text. | Deleted, for a pair with an erased bead. |
| `arrows` | The arrows a judge may give: each one's type, forward and inverse names, whether it ends currency or disputes, and where its names come from (see [The arrows](#the-arrows)). | Not erased. |
| `edges` | A relationship from one bead to another: its arrow and the arrow's type, the two IDs, `seq`. | Kept. They hold IDs. |
| `pending_chains` | An arrow that replaces or withdraws, which a judge gave and which waits for a second read: IDs only. | Deleted. |
| `name_links` | Two names for one entity, each as a byte range of its own chunk, with the scope of each chunk's bead, `seq`. | Deleted, for a link with an erased bead. |
| `work` | What is still owed: chunks and tool calls to be read, beads to be judged, arrows to be read a second time. IDs, attempts, when to try next and a problem code. No text. | Deleted. |

Text is kept in one place on purpose. Erasing a message deletes its
`chunk_text` rows and every row derived from that text, in one transaction.
Tables that survive an erase hold IDs, ranges and engine-side times only.

One table holds text that a source did not write. `tool_outcomes` holds two
short lines a model wrote about a tool call (see [Tool calls](#tool-calls)).
They say what the call's text says, so they are erased with it, and like
source text they are kept out of the planner's statistics.

Two more columns are computed from what a source said and are treated as
its text is. A verdict's `columns` say, among other things, how many
identifiers two beads share, and a name link's ranges say where a name
stands. Both are deleted with either bead, and `columns` is kept out of the
planner's statistics.

Rows are immutable. Triggers refuse updates and deletes on `events`,
`chunks`, `chunk_text`, `beads`, `pins`, `erasures`, `outbound_calls`,
`outbound_results`, `chunk_reads`, `chunk_read_spans`, `tool_outcomes`,
`judge_reads`, `verdicts`, `arrows`, `edges` and `name_links`, and refuse
`TRUNCATE` on every table above. The one exception is an erase, and the
triggers allow exactly what an erase does: an event or a bead goes from
live to erased and keeps its engine-side columns; text, reads, spans and
tool outcomes are deleted only for an event that was just erased; and a
verdict or a name link is deleted only when one of its beads is erased. A
scope keeps its name and workspace. The triggers stop mistakes, including
the table owner's. They are not a defence against the owner, who can drop
them.

The same tables refuse a new row about what was erased, whoever writes it:
a read or a tool outcome only for an event that is live, and a verdict, an
edge, a held arrow or a name link only for beads that are live and
of the row's own workspace. An erase would not reach such a row again. A
name link's ranges lie inside the text of their chunks.

The graph keeps its shape in the table too. An edge joins two beads of its
own workspace and points at a bead of source content, and its arrow is one
of the directory's, of that arrow's type. Only a group bead `consists` of
members; every other arrow joins two beads of source content. A bead is
replaced or withdrawn at most once, a statement ends at most two beads and
with one arrow, and only a current statement ends a current bead, so a
chain grows at its head and no circle can close. Two writers that end beads
at once are put one after the other: each locks the two beads of its edge,
in one order, before it looks, so the second sees what the first wrote.

`work` is the one table the engine changes in place. It is a queue, not a
record: a row says what is still owed and is deleted when it is done.
`pending_chains` is the same kind of thing: a row is an arrow that waits,
and it is deleted when a second read settles it.

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
workspace in `events`, `beads`, `erasures`, `outbound_calls`,
`chunk_reads`, `tool_outcomes`, `judge_reads`, `verdicts`, `edges` and
`name_links` carries `seq`, the next number from that counter. Taking a
number locks the counter until the transaction ends, so:

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
hashes and metadata, marks its beads erased, and writes the journal. It
also deletes what a judge computed from the message's content: see
[what an erase does to relationships](#what-an-erase-does-to-relationships). IDs, ranges and links
stay as the tombstone. Offering the event again is refused, and so is any
later row about it: the tables that hold what a model read or a judge said
take rows only for an event, or for beads, that are live.

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
| `core_memory_engine` | Read and write the data tables. It reads model consent and cannot give it. What it stored it cannot change or delete, with two exceptions: its queue (`work`), and an arrow that waits for a second read (`pending_chains`), which it deletes once that read is stored. The engine's login role is a member. |
| `core_memory_governor` | Execute the governance functions, which include giving and withdrawing model consent. Holds no table privileges. |

Both are created by the first migration if they do not exist. Every table
has row security enabled, with a policy for `core_memory_engine` only. A
host that reads beads or text adds policies for its own roles. The one
other policy is on the history of migrations, which holds versions and
checksums and no data: any role granted to read it reads all of it, as it
must to learn the store's version.

A judge reads across the scopes of a workspace, so a verdict or a name link
can be about beads of two scopes, and both were computed from what those
beads say. Each such row therefore carries the scope of each of its beads
(`earlier_scope` and `later_scope`; `a_scope` and `b_scope`), read from the
beads when it is stored. A host's policy can decide from the row alone who
may read it: a role that reads one scope reads what was judged inside it.
Edges and the beads a judge made hold IDs and one of a fixed set of words,
and carry no scope.

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
that names a `[[binding]]`. A model reads each tool call once a tool reader
is: a `[tool_reader]` section. A model judges how beads relate once a judge
is: a `[judge]` section, described under [Relationships](#relationships).
Without any of them the engine calls no model, and everything else works
the same.

### What a read is

The model is shown a batch of chunks, all of one scope and one source
profile, under handles (`C1`, `C2`, ...). The types it may choose from
follow its instructions, so every batch of one vocabulary starts with the
same text (prompt revision 5). For each chunk it answers with:

- **a type**: one key of the vocabulary, or an abstention (`no_fit`,
  `insufficient_evidence`, `ambiguous`). An abstention is a completed
  judgment. It is stored and is not retried. The chunk is kept, judged and
  found as any other; only its type is missing. Which messages have a type,
  small talk among them, is the vocabulary's to say: the prompt has no rule
  of its own about them;
- **entities, actions and outcomes**, each as a quote of the chunk. An
  entity has one of memoriesQL's entity kinds (person, organization, group,
  place, project, product, system, agent, artifact, event), or `other` for
  a named thing that is none of them.

A quote is looked up in the chunk and kept as the byte range where it was
found. Nothing the model wrote is stored: a type is a key of the
vocabulary, and a span is a range of text the source wrote. A quote that is
not in the chunk is not a finding. A quote that is nowhere in the chunk
exactly is looked for again loosely (validator revision 2):

- runs of whitespace are read as one space;
- curly quotation marks and apostrophes, dashes and an ellipsis are read as
  `"`, `'`, `-` and `...`;
- invisible characters are left out.

Models write a straight apostrophe for a curly one, and a space for a line
break. Either way the range kept is of what the source wrote.

A reply that breaks the shape is rejected whole and repaired once: the
model gets its reply back with what was wrong. On that second attempt a
quote that is still not found is dropped and counted (`spans_rejected`)
rather than failing the batch. A source profile's `types` narrows which
types its chunks may be given.

The problems quote the reply, so they go back to the model and nowhere
else. The round's log line lists what kinds of thing its rejected replies
had wrong (`reply_faults`), a first reply that the repair put right
included: `not_json`, `shape`, `unknown_chunk`, `unknown_type`,
`unknown_abstention`, `unknown_entity_kind`, `answered_twice`,
`missing_chunk`, `quote_not_found` and `quote_limit` (a quote too long, or
too many of one kind). It also counts the quotes the stored reads left out
(`quotes_dropped`). Together they say what a repair cost was spent on.

Every read records its reader: the binding's ID and model, the prompt
revision, and `binding_rev`, a hash of everything that affects what the
model says (provider, endpoint, model, sampling settings, output limit, the
prompt, the reply shape and the validator), plus the vocabulary's hash.
Timeouts, budgets and credentials are not part of it. Reads are added,
never changed: the read with the highest `seq` for a chunk is the current
one.

### Tool calls

A tool call is an event whose content is `{"tool_call": {"tool", "input",
"output", "is_error"}}`, admitted under a profile for tool calls such as
the built-in `core.tool_call`. Its text is kept whole like any other: the
arguments in canonical JSON, a line feed, and the output byte for byte. The
event records the tool's name and where the arguments end.

Among beads a tool call is one, with the event's own ID, however many
chunks its text was cut into. Its chunks are not read one by one. The call
is read whole, once, in a model call of its own.

Code shortens the text first, and the model is shown everything that is
left, with no limit on its length. The compressor (`tool.lines`, revision
1) applies three fixed rules, to the arguments and to the output:

1. three or more consecutive lines that are the same, or the same but for
   their numbers, become the first of them and a count. A number that is
   zero is only like another zero, and a line that reports trouble only
   like another that does, so `0 failed` never stands for `12 failed`;
2. of what remains, the first 40 and the last 40 lines are kept, each with
   its count, and between them only lines that report trouble (`error`,
   `fail`, `panic`, `exception`, `traceback`, `warning`, `fatal`, or an
   exit status other than zero anywhere in the line);
3. a line longer than 1,000 bytes keeps its first 600 and its last 200.

Nothing is rewritten. Every line shown is a line of the call, a shortened
line of the call, or a marker in square brackets that says how much was
left out. A marker is written only where it takes less room than what it
stands for, so what is sent is never longer than what is stored. The text
is read once, front to back, and what is held is what will be shown.
Changing a rule means a new compressor revision.

The model answers with three things, and these are what is stored:

- **action**: what the call did, in at most 120 characters;
- **outcome**: what came of it, in at most 200 characters;
- **status**: `succeeded`, `failed`, `partial` or `unknown`. `unknown` is a
  completed judgment, not a failure to answer.

Each is one line of plain text: it shows at least one character, and holds
none that would end the line or turn the direction of the text around it.
A reply of another shape is rejected and repaired once, as a read of chunks
is. This is the one place the engine
stores text a model wrote: a later reader is given this record in place of
the tool's own text, which stays in the store and is not sent again.

A tool outcome records its reader as a read does, and the compressor's ID
and revision with it. The reader's `binding_rev` covers the compressor too,
so a tool reader and a chunk reader on one binding are different readers.
Outcomes are added, never changed: the one with the highest `seq` for a
call is the current one.

The call's receipt names every chunk of the event, and its byte count is
of the text as it was sent, after shortening. The budget counts the same.
If the stored text does not hold what its event says it holds, no call is
permitted, no receipt is written, and the work is set aside.

### The four steps of a call

1. **Permit.** One transaction, starting with `begin_write`, checks that
   the scope is active and has granted the binding's destination, checks
   the budget, reads the text, and writes the call's receipt in
   `outbound_calls`. A refusal sends nothing and writes no receipt. The
   receipt is committed before any text leaves the engine.
2. **Call** the model through `ModelTransport`: one request, with no hidden
   retry or fallback. The adapters send over HTTPS, through the proxy that
   `HTTPS_PROXY` names unless `NO_PROXY` lists the host, and check the
   server against the public web roots plus an optional private CA
   (`[models] ca_file`).

   A request's instructions are the same for every call of a reader, a
   tool reader or a judge, and come first. The Anthropic adapter marks
   them for the provider's prompt cache. What a call reads from the cache
   costs a tenth of the input price, and what it writes there a quarter
   more than that price; an entry lasts five minutes past its last use. A
   judge's instructions are most of what it sends. Instructions too short to cache
   are sent the same way and are not cached. OpenAI caches a long prompt's
   start by itself. Either way the round's log line counts the input tokens
   read from and written to the cache (`cache_read_tokens`,
   `cache_write_tokens`), which are part of `input_tokens`. Caching changes
   nothing a model says, and is not part of a binding's revision.
3. **Validate** the reply against the closed shape above. The engine checks
   every reply itself. It asks the provider to hold the judge's and the
   tool reader's replies to their shape, and not the chunk reader's: held
   to it, Claude Haiku 5.5 typed every chunk, small talk included, and
   quoted nothing (`eval/reader-probe`).
4. **Finish.** One transaction records how the call ended in
   `outbound_results`, stores the reads and their spans, or the tool
   outcome, and settles the queue. A read is stored only for a chunk the
   receipt names whose text still exists and whose scope is still active,
   and a tool outcome only for the event the receipt's chunks belong to,
   on the same terms. The tables themselves take a read or an outcome only
   for an event that is live, whoever writes it.

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
configured, and one for each tool call while a tool reader is.
`core-memory read queue <scope>` adds what was stored before that, and
with `--reread`, what was only read by another binding revision than the
configured one or, for chunks, with another vocabulary.

A reader claims a batch of chunks, or one tool call, with `FOR UPDATE SKIP
LOCKED`, so readers never wait for each other, and holds it under a lease.
A reader that dies leaves its work to be claimed again when the lease runs
out.

Each kind of work (chunks, tool calls, beads to judge) is done in a lane of
its own: a thread with its own store connection and transport, so a slow
call of one kind holds no other back. Chunks get `reader.workers` lanes (1
by default, at most 16), each reading a batch at a time, so a backlog is
read that many times as fast, as far as the provider's rate limits allow.
Lanes share nothing but the store. Two lanes never get one item, and a
day's budget is checked under the workspace's lock, so lanes together
spend no more than one would. Changing the number of workers changes no
read's identity.

| What happened | What becomes of the work | `problem` |
|---|---|---|
| The reply was valid | Deleted. The reads are stored. | |
| The scope has not granted the destination | Put off, and due again when it is granted. | `no_consent` |
| The scope is paused or revoked | Put off, and due again when it is active. | `scope_not_active` |
| The day's budget is spent | Put off until the next UTC day. | `over_budget` |
| The provider did not process the request | Tried again after `retry_backoff_ms`, or the provider's own delay. | `provider_unavailable` |
| The request may have been processed | Tried again after `retry_backoff_ms`. | `call_in_doubt` |
| The model declined | Tried again alone, one chunk to a call. A chunk that was sent alone, and a tool call, is set aside at once: the same request would get the same answer. | `model_refused` |
| The request did not fit the model's context, or the reply was cut off at `max_output_tokens` | Tried again alone, as above. | `did_not_fit` |
| The reply was invalid twice | Tried again alone. | `reply_invalid` |
| A chunk of the batch was erased before the repair | Nothing more is sent. The rest is tried again after `retry_backoff_ms`. | `batch_changed` |
| The provider turned the request itself down | Tried again after ten minutes. An operator has to fix the binding. No lane of that kind (chunks, tool calls or judging) makes another call for those ten minutes: a wrong key or model fails every call alike. | `binding_rejected` |
| A tool call's stored text does not hold what its event says | Set aside at once. Nothing is sent. | `text_unreadable` |
| The work was handed out `max_attempts` times and never settled | Set aside, when it would have been handed out again. | `attempts_spent` |

Work that was put off before any call was made does not count as an
attempt. Once a call was made for it, the attempt counts whatever comes
next: a repair that is not permitted leaves the work put off and one attempt
poorer. After `max_attempts` calls an item's work is set aside as `failed`,
until `core-memory read retry` puts it back.

The count is also kept where work is handed out. A reader that dies with
its work, or cannot record how its call ended, leaves a lease that runs
out. That work is handed out again, and the call made again, but only
until its attempts are spent. Then it is set aside as `attempts_spent`. No
call is made more than `max_attempts` times for one item, whatever fails.
`core-memory read status` shows the counts, one line for each kind of
work: chunks, tool calls, beads to judge and arrows to read a second time.

`core-memory serve` runs the lanes beside the server for as long as it
serves. `core-memory read run` runs them all at once and stops when no lane
has anything due. A lane that finds nothing waits while others work, since
reading a chunk can make its bead due to be judged.

### What is never written down

The queue, the receipts, the results and the logs hold IDs, counts and
problem codes. They hold no source text and nothing a model or a provider
said: a provider's error message can quote the request. What a model wrote
about a tool call is in `tool_outcomes` and nowhere else. Of what a judge
wrote, nothing is kept as text at all: a verdict is one of a fixed set of
words, an arrow is a key of a fixed directory, and a name is a range of a
chunk.

## Relationships

A judge model reads each new bead against the beads near it in time, from
every scope of its workspace, and says how they relate. It runs once a
`[judge]` section names a `[[binding]]`. Code computes what the judge is
shown and records what it says. Code never proposes an edge and never picks
a pair: a bead is shown because of when it happened and for no other
reason, and a score is never evidence.

### The arrows

The relationships are those of Mark Burgess's Semantic Spacetime, as his
SSTorytime implements them. Every relationship is an *arrow*, and every
arrow belongs to one of four types of association:

| Type | Burgess | What it says |
|---|---|---|
| `near` | 0, proximity | The two are close: alike, about one matter, or easily confused. No direction. |
| `leadsto` | 1, follows | One follows from the other: cause, result, dependency, implementation, replacement. |
| `contains` | 2, membership | One is a part of the other, or both are parts of one memory. |
| `express` | 3, expresses | One states something of the other: a note, an example, an attribute, a definition, a source. |

An arrow has a forward name and, unless it is symmetric, an inverse, as in
his `SSTconfig` files: `cause` reads "causes", `cause-by` "is caused by".
It is stored by its forward name, from the bead the forward name is said
of: `A cause B` is an edge from A to B. A judge names the arrow from the
new bead's side, the forward name or its inverse, and the name says which
way it runs, so there are no direction codes to settle separately. The
arrows of `near` have one name and no direction, and are stored from the
smaller ID to the larger. A `near` arrow whose name starts with `!` says
that two are *not* alike, as his files mark it: nearness is never completed
through it.

| Arrow | Inverse | Type | A judge gives it when | Names from |
|---|---|---|---|---|
| `cause` causes | `cause-by` | leadsto | what one describes brought about what the other describes | SSTorytime |
| `result` results in | `result-of` | leadsto | the other is what came of an action, a fix, a call or a test | SSTorytime |
| `enables` | `enabled-by` | leadsto | one had to be in place for the other to happen | SSTorytime |
| `implem` implements | `implorg` | leadsto | a step carries out a decision, a plan or a request | SSTorytime |
| `repl` replaces | `repl-by` | leadsto | a later statement on the very matter replaces the earlier: a changed date, a corrected figure, a reversed decision | SSTorytime |
| `withdraws` | `withdrawn-by` | leadsto | a later statement takes the earlier back and puts nothing in its place | engine |
| `has-pt` has a part | `pt-of` | contains | one is a whole, a plan, a list, a meeting, and the other one of its items | SSTorytime |
| `same-memory` | | contains | the two are pieces of one message or document, or of one account its author runs on | engine |
| `consists` consists of | `mkpt` | contains | not given by a judge: a group bead's edge to each member | SSTorytime |
| `note` note/remark | `isnotefor` | express | one is a note or remark about the other | SSTorytime |
| `e.g.` has example | `ex` | express | one is an example of what the other says | SSTorytime |
| `i.e.` in other words | `clarify` | express | one says the other in other words | SSTorytime |
| `attr` has an attribute | `attr-of` | express | one states an attribute of what the other is about | SSTorytime |
| `def` defined as | `def-of` | express | one defines what the other names | SSTorytime |
| `source` has source | `source-of` | express | one is where the other came from | SSTorytime |
| `solution` potential solution | `issolution` | express | one is a potential solution to a problem the other states | SSTorytime |
| `answers` | `answered-by` | express | one answers a question the other asks | engine |
| `confirms` | `confirmed-by` | express | a later statement says again what the earlier says | engine |
| `see` see also | | near | both are about one particular matter, and nothing above holds | SSTorytime |
| `sim` similar to | | near | the two say much the same of different matters | SSTorytime |
| `!confuse` don't confuse with | | near | the two are easily taken for one, by a name or the same words, and are not | SSTorytime |
| `!contradicts` | | near | the two cannot both hold, and neither replaces or withdraws the other | engine |

The directory is the table `arrows`, which a host may read; it changes only
with a migration. Five arrows are this engine's own, written in the form his
files use for a user's additions: `withdraws` (a promise withdrawn, in his
Promise Theory), `same-memory`, `answers`, `confirms` and `!contradicts`.

Two arrows **end currency**: `repl` and `withdraws`. A bead of source
content is **current** while no `repl` or `withdraws` edge points at it.
Nothing else makes a bead stop being current. In particular a
contradiction does not: Burgess keeps a contradiction as a difference that
each observer assesses for itself, and so does the engine. Both statements
of a `!contradicts` stay current, and the dispute is **open** for as long as
they both are.

What is taken from his implementation and what is not:

- **The four types and named arrows within them**, with forward and inverse
  names, are his. His short names are used as he gives them.
- **Sequence is the store's, not a judge's.** His `(then)` arrow, and the
  `_sequence_` sections his text tool writes, join each event to the next
  in the order of the stream. The store holds that order already, as each
  bead's time and `seq`, so a judge adds only what order alone does not
  say. A relationship is never made from chronology alone.
- **Nearness is not completed.** SSTorytime closes cliques of `near` arrows
  (A near B and B near C makes A near C). His own paper on knowledge
  representation holds that transitivity cannot be promised for an
  arbitrary association, and invariant 4 says a relationship is judged,
  never inferred. Completing nearness is a view a host may compute.
- **A link has no context and no weight here.** His links carry both. The
  context of a judgment is its read: the call, which names the beads shown
  and their scopes. Weights are a host's.
- **One arrow for a pair in a read**: the one a reader most needs. His
  graphs allow several, and a pair judged again may gain another.
- **`wrongabt` is not offered.** "Is wrong about" asserts which side is
  wrong, which a judge cannot know; a dispute is recorded as
  `!contradicts`.

Sources: Burgess, *Spacetimes with Semantics (III)* (arXiv:1608.02193),
section 5.4.9 and table 1, and section 5.6.3 on negative associations; *A
Spacetime Approach to Generalized Cognitive Reasoning in Multi-scale
Learning* (arXiv:1702.04638), table I; *Testing the Quantitative Spacetime
Hypothesis using Artificial Narrative Comprehension (II)* (arXiv:2010.08125),
the four types as applied to narrative; *Spacetimes with Semantics (I)*
(arXiv:1411.5563); the *Book of Promises*
(`markburgess.org/BookOfPromises.pdf`) on incompatible and withdrawn
promises; and his code: `pkg/SST/SST.go` of
SemanticSpaceTime, and `SSTconfig/arrows-*.sst` and `docs/N4L.md` of
SSTorytime.

### What the judge writes

| Thing | What it is |
|---|---|
| Verdict | One row for every pair the judge was shown, whatever it said: the type of the arrow it gave (`near`, `leadsto`, `contains`, `express`) and the arrow, or `none`, or `unsure`. It holds no text. `unsure`, and a pair that was never shown, are not a no. |
| Edge | An arrow from one bead to another, with its type. An edge has no state and no weight, and is never changed or deleted. The same arrow between the same two beads is one edge, however often it is judged. |
| Group bead | A bead that marks several beads as parts of one memory, in one message or across messages: a hub, as Burgess builds one in his narrative work. `consists` edges run from it to each member. |
| Name link | Two different names for one entity, each kept as the byte range where it stands in its own chunk. The same two names linked again are one link. |

A chain has one head and never branches: a bead is replaced or withdrawn at
most once, and a statement ends at most two beads, all with one arrow.
The edge runs from the later statement itself to each bead it ends. The
beads of an open dispute are both shown to the judge first, for as long as
the dispute is open.

### A judge read

**What is queued.** Admission queues each chunk's bead for the judge, due
after `debounce_ms`, so that what arrives together is judged together. A
tool call's bead is queued when a model's record of the call is stored: a
judge is shown that record and never the tool's own text. `core-memory
read queue <scope>` queues the beads no judge has read, and with
`--reread`, those that only another judge than the configured one has.

**What one read takes.** One judge reads in a workspace at a time. A read
takes the bead that has waited longest and the beads waiting with it whose
time lies within half of `window_ms` of its own, up to `max_new_beads`, so
that each lies in the window of the others. A chunk's bead waits for its
chunk to be read, so the judge is shown its type; it waits at most
`max_wait_for_reads_ms`, and not at all when no reader is configured. A
bead that was part of a read that failed is read alone from then on.

A bead that comes up with no other to be read with, because none lies near
it or those that do are still owed a judgment themselves, is no pair. It is
settled without a call: no receipt is written and nothing is counted
against a budget. Its pairs are read when the beads near it are judged,
with it in their window. It was never read as new, so `read queue` queues
it again, and it is settled again without a call unless a pair is still
unread.

The work a read was handed out is held under a lease, and whatever settles
or stores for it names that lease. A worker whose lease ran out, and whose
beads were handed to another, stores and settles nothing for them when its
reply comes at last.

**What it is shown beside them.** The window is chosen inside the permit,
under the workspace's lock, by time alone: the live beads of source content
whose time lies between the new beads, or within `window_ms` before the
earliest or after the latest, nearest first, up to `max_window_beads` beads
and `max_window_bytes` of content. The beads of open disputes come before
them, most recent first, up to `max_conflict_beads`. A bead that is itself
waiting to be judged is left out. Its pairs are read when its own turn
comes, with these beads in its window, so a pair is read once, whichever of
the two arrived first. So is a bead this judge has already read with every
new bead: that happens when beads stored before there was a judge are
queued after later ones were read beside them. A pair the limits kept out
of every window was never shown, and nothing is recorded for it.

Each bead is decided on its own: it is shown only while its scope is active
and has granted the judge's destination. The permit locks the workspace,
reads the text and writes the receipt, as it does for any other model call
(see [The four steps of a call](#the-four-steps-of-a-call)). A judge's
receipt names every bead shown and has no scope of its own. The beads of a
repair, or of a second read, are named, and are shown all together or not
at all: if one may not be shown, nothing is sent, no receipt is written,
and the work waits.

**How a bead is shown.** Each bead has a handle: `B1`, `B2`, ... for the
window and `N1`, `N2`, ... for the new beads, each list in time order.
With it come its time and how precise that is, the kind of source, opaque
handles for its scope, its author, its message, its thread and its
session, where it stands in its message, what it replies to if that is in
the lists, its type if a model read one, whether it is still current, the
arrow by which it already replaced or withdrew another (`chain`), and its
group. A message's bead is shown with its chunk's text. A tool call's bead
is shown with its tool, and the action, outcome and status a model wrote of
the call.

**What it answers.** One entry for each new bead: the pairs that have a
relationship, each with an arrow, and the pairs it cannot decide. A new
bead is compared with every bead of the window and every new bead before
it. A pair it leaves out is a `none`: it compared the two and found
nothing. The arrow is named so that "the new memory *arrow* the other" is
true, which settles its direction: `repl` when the new bead replaces the
other, `repl-by` when the other, a later statement that arrived first,
replaces it.

The rubric also says what a relationship is not. That one message answers,
thanks or reacts to another, or comes next in a conversation, is already
kept as structure (`reply_to`, thread, session, the times), and is not a
relationship by itself: that one replies to or mentions the other is not
`leadsto`, and a question and its answer are `answers`, not `leadsto`. The
first live run (`eval/locomo-smoke`) showed why: without that rule, most of
what the judge wrote joined a message to the reply next to it.

Since rubric revision 3, messages by different people are never parts of
one memory, and two statements that give different values for one fact are
related whether or not either mentions the other. Since revision 4, which
brought the arrows, that relationship is `repl` from the later statement
when it changes or corrects the earlier, and `!contradicts` when either
could still be right: both then stay current. Under revisions 2 and 3, a
contradiction was a label on the chain, which made the later statement
current by recency; in six runs of `eval/adversarial` the judge called such
a pair `express` five times.

A pair gets one arrow: one that ends currency if one holds, else
`same-memory`, else another of `leadsto`, else one of `contains`, else one
of `express`, else one of `near`.

The provider holds the reply to its shape: an arrow is one of the
directory's names, forward or inverse, of every arrow a judge may give. A
provider holds an enum value to its list but not to its case, so an arrow is
read whatever its case, as a tool call's status is. The adversarial run of
2026-11-20 (`eval/adversarial`) lost 46 of its 64 judge calls to replies
the provider let through and the validator refused, under the codes and
labels of revision 2; revision 3 made the shape exact, and revision 4 keeps
it so with one field.

A reply is rejected whole, and repaired once, if it is not of that shape,
if a new bead has no entry or a pair two answers, or if what it says would
break a rule of the store:

- an arrow is not one a judge may give;
- a bead that is no longer current replaces or withdraws, or is replaced or
  withdrawn;
- a bead is ended twice, a statement ends more than two beads or both
  replaces and withdraws, or statements end each other in a circle;
- beads are tied into one memory by `same-memory` answers without each pair
  of them being `same-memory`, or members of two groups would become one;
- a name link joins beads that were not compared, or a tool call, which
  has no text to copy a name from.

A name that is not found word for word in its bead's text, or two names
that are the same, fail the first reply. On the repair they are dropped and
counted (`names_rejected`), as an unfound quote of a chunk read is. If the
repair is rejected too, nothing is written, the pairs stay unjudged, and
the beads are tried again alone.

The problems quote the reply, so they go back to the model and nowhere
else. What kind each one was is a code from a fixed set, and the round's
log line lists the kinds its rejected replies had (`reply_faults`), a first
reply that the repair put right included: `not_json`, `shape`,
`unknown_memory` (a handle the entry may not answer for or about),
`relationship` (an arrow not allowed), `answered_twice`, `missing_entry`,
`not_current`, `chain`, `group` and `name`.

### What a read stores

One transaction, under the workspace's write lock, stores a row for the
read itself, a verdict row for every pair, the edges, the group beads with
their edges, and the name links. Something is stored only for a bead the
call's receipt names, that is still live, in a scope that is still active,
and whose work this call still holds under its lease: a bead erased while
the judge was reading, or handed to another worker, gets nothing. A new
bead whose scope stopped being active is put back in the queue, its attempt
given back, so that no lease is left behind to keep other judges out of the
workspace.

A verdict row holds the two beads in time order, the scope of each, the
arrow and its type, an explicit direction, the rubric and binding
revisions, and the pair's `columns` as they were at that moment: the gap in
time, whether the beads share a scope, an event, a thread, a session or an
actor, whether one replies to the other, how many identifiers and URLs
they share, their kinds and types, and whether each was current and new to
the judge. The columns are numbers, flags and vocabulary keys. They are for
a host that learns which pairs are worth showing a judge. Nothing in the
engine reads them back.

A chain grows at its head. When a read is stored, an arrow that ends
currency is written only if its source and each bead it ends are still
current, and its source ends no beads with the other arrow, has room for
these, and does not already wait with another held arrow. Otherwise the
verdict is kept and noted: `not_current`, or `chain_taken`.

An arrow that ends currency changes what a reader takes as current, so it
is not written by the read that gives it. It is held, its verdict is noted
`awaits_second_read`, and the pair is read a second time: the source and the
beads it would end, alone, without the first answer. If the second read
gives the same arrow for the same beads, it is written. If it does not,
nothing changes, and its verdict is noted `second_read_differs` when its
arrow too would have ended a bead. A second read stores its verdicts and at
most that arrow, and nothing else. This rule is the setting `second_read`,
which is on unless it is turned off. Every other arrow, `confirms` and
`!contradicts` included, is written by the read that gives it.

Arrows that share a bead are settled in order. An arrow that ends currency
and shares a bead with one that waits for its second read waits too:
written at once, a withdrawal of a statement whose own replacement waits
would end that statement first, and the replacement could then never be
written. Held arrows are read a second time in the order they were held,
and one that shares a bead with an earlier held arrow waits for it, unless
that one was set aside as failed. A chain given in one read is so written
from its oldest statement on. A held arrow that can no longer be written
when its turn comes, because a bead it is about was ended meanwhile or its
source took the other arrow, is dropped without a call, and its first
verdict keeps its note.

A group is what the judge affirmed pair by pair with `same-memory`. A new
bead joins the one group its partners already share, or a new group is made
of beads that belong to none. Nothing becomes part of a memory by way of a
third bead. A `has-pt` arrow makes no group: the whole and its part stay
memories of their own.

A pair is read once by a judge. When beads are queued again for another
judge, with `read queue --reread`, its verdict on a pair is a new row
beside the first, under its own binding revision. Neither replaces the
other: a later `none` is evidence against an edge for whoever weighs edges,
and the edge stays. An arrow both judges give is one edge, a name link both
give is one link, and a second judge cannot end a bead that is already
ended.

The judge's work follows the same rules as a reader's (see
[The queue](#the-queue)): put off without counting an attempt when no bead
may be shown, counted once a call was made, tried alone after a failed
read, and set aside after `max_attempts`.

### What an erase does to relationships

Erasing an event erases its beads as before: they stay, as IDs. For what a
judge said of them:

- **Verdicts** about an erased bead are deleted. Their columns were
  computed from its content.
- **Name links** to an erased bead are deleted, and so is an arrow that
  waits for its second read, with that read.
- **Edges stay.** They hold IDs, and a link to a tombstone says only that
  there was one. So what an erased statement replaced or withdrew does not
  become current again, and erasing a bead in the middle of a chain leaves
  the chain as it was.
- **A group bead** is marked erased when its last live member is.

After an erase the tables take no new verdict, edge, held arrow or name
link about the erased bead, whoever writes it.

### Pairs beyond the window

The engine's judge reads a bead only against the beads near it in time.
Finding pairs across the whole store, ranking them and judging them is a
host's work: memoriesQL does it over the tables it reads, with judges and
tables of its own. The engine keeps nothing for it but what any host may
read: every verdict, a no included, with the pair's columns.

## Records and export

`core_memory.record.v2` is the public JSON form of the engine's primitives:
`event`, `chunk`, `chunk_read`, `tool_outcome`, `verdict`, `bead`, `edge`,
`name_link` and `erasure` records. `core-memory export` writes them as JSON
Lines, one workspace after another, each in `seq` order: an event with its
chunks, then what a model read in them or wrote about the tool call as it
was read, what a judge said as it was stored, and an erasure where an event
was erased. A read by a judge is exported as its verdicts first, one for
each pair, and then what they amounted to: the edges that end currency,
the other edges, the group beads with their edges, and the name links. A
`bead` record is a group bead a judge made; a bead of source content is its
chunk, or its tool call's event, under the same ID. An `edge` record names
its arrow and the arrow's type.

An erased event's own records, and what was read in it or judged of it, are
not exported. An edge is: it holds two IDs. Each record carries its `seq`,
and `export --workspace W --after N` resumes after it.

A judge reads across the scopes of a workspace, so what it says can join
beads of two scopes. `export --scope S` leaves such records out: it gives
only what lies wholly inside the scope, and does not say that another
exists. A record of what a judge said has no one scope of its own. A
`verdict` and a `name_link` record name the scope of each of their beads.

The tables are the truth; records are how they are published outside SQL.

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

Two checked-in files pin what others depend on. A difference in either
fails a test, and is read before it is accepted:

- `tests/golden/export.jsonl` in the store crate: the records exported for
  a fixed set of events, with every ID, hash, range and judgment.
- `tests/golden/schema.txt` beside it: what a host sees of the
  `core_memory` schema. Every table and column, what each role may do with
  it, its row security, triggers and indexes, and every function with who
  may call it.
