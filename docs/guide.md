# Core Memory guide

How to install, run and operate Core Memory 2: the command, the HTTP
service, model readers and the judge, recall, erasure, and running the
engine inside your own database. The [README](../README.md) is the short
version; [architecture.md](architecture.md) is the design behind it.

- [Install](#install)
- [Try it](#try-it)
- [Workspaces and scopes](#workspaces-and-scopes)
- [Over HTTP](#over-http)
- [Checkpoints](#checkpoints)
- [Reading with a model](#reading-with-a-model)
  ([your ChatGPT plan](#using-your-chatgpt-plan), [tool calls](#tool-calls))
- [Judging how memories relate](#judging-how-memories-relate)
- [Recalling](#recalling)
- [Erasing](#erasing)
- [Running inside another system's database](#running-inside-another-systems-database)
- [Connecting to the database](#connecting-to-the-database)
- [Upgrading](#upgrading)
- [Measured](#measured)

## Install

The `core-memory` command is on PyPI as `core-memory`. Each wheel carries
the compiled engine for Linux (x86_64 and arm64) or macOS (Intel and Apple
silicon), so no Rust toolchain is needed:

```bash
uv tool install core-memory     # or: pipx install core-memory
core-memory --version           # core-memory 2.0.1
```

The engine needs PostgreSQL 15 or newer. The package holds only the command:
`import core_memory` fails, and says to pin `core-memory<2` for the 1.x
Python library. There are no Windows wheels and nothing on crates.io; to
build the command from source, see [Contributing](../README.md#contributing).

## Try it

Start a local PostgreSQL with Docker:

```bash
docker run -d --name core-memory-pg -e POSTGRES_PASSWORD=secret \
    -p 127.0.0.1:5432:5432 postgres:18
export CORE_MEMORY_DATABASE_URL=postgresql://postgres:secret@127.0.0.1:5432/postgres
```

Then get the example events and their config, and run them:

```bash
git clone https://github.com/JohnnyFiv3r/Core-Memory
cd Core-Memory/examples/quickstart

core-memory migrate                            # create the core_memory schema
core-memory scope add acme --workspace demo    # allow events for scope "acme"
core-memory ingest --input events.jsonl        # one outcome per input line
core-memory export                             # what is stored, as JSON Lines
core-memory recall Northwind renewal           # the text that holds those words
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

[[http.token]]
id = "assistant"
sha256 = "<from core-memory token>"
recall = ["acme"]
```

A token writes to the scopes in `scopes` and recalls from those in `recall`.
Either may be `["*"]` for every scope. A token that writes does not read
back unless it is given `recall`.

| Endpoint | |
|---|---|
| `POST /v1/events` | Admit one SourceEvent. The body is the typed outcome. An accepted event is committed before the answer is sent. |
| `POST /v1/events/batch` | `{"events": [...]}`. One outcome per event, in order. Read each one. |
| `GET /v1/checkpoints?scope=&key=` | A reader's stored position. |
| `POST /v1/recall` | `{"query": "..."}`. The stored text that holds the words, with what a judge said of it. See [Recalling](#recalling). |
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
destination, which is the host of the binding's endpoint (for a binding paid
by your own ChatGPT plan, `signed-in@` and the host):

```bash
core-memory check                                 # prints each binding and its destination
core-memory scope allow acme --binding small      # consent, for this scope and this destination
core-memory read run                              # read what is due now, then stop
core-memory read status                           # queued, being read, failed
core-memory scope disallow acme --binding small   # no later call is permitted
```

`core-memory serve` reads in the background while it serves. Chunks admitted
while a reader is configured are queued by themselves. For what was stored
before, run `core-memory read queue <scope>`. Reading, reading tool calls and
judging each run on threads of their own, so a slow judge never holds reading
back. To read a backlog faster, give the reader more workers, as many batches
at once as your provider's rate limits allow:

```toml
[reader]
binding = "small"
workers = 4          # 1 by default, at most 16
```

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
When the provider turns a binding down, as it does a wrong key or model, the
command makes no other call of that kind for ten minutes.
Logs and receipts never hold chunk text or what a model said.

Model calls go through the proxy that `HTTPS_PROXY` names, except to the
hosts `NO_PROXY` lists. If that proxy re-signs what it passes on, or a model
server of your own has a private certificate authority, name its
certificates:

```toml
[models]
ca_file = "proxy-ca.pem"   # trusted as well as the public roots
```

Without it, a call to a server whose certificate is not trusted sends
nothing: `core-memory read status` shows its work put off with the problem
`endpoint_untrusted`, and no call of that kind is tried for ten minutes.

### Using your ChatGPT plan

With a ChatGPT Plus or Pro account, the reader, the tool reader and the judge
can use your plan instead of API credits. This works with OpenAI's models only;
an Anthropic binding still needs an API key.

Sign in once, on the machine that runs Core Memory:

```bash
core-memory login chatgpt
```

It first asks whether Core Memory may use your plan in the background, and
signs nothing in unless you answer `yes`. Then it opens your browser at
OpenAI's sign-in, where Core Memory registers under its own name, and waits
for the browser to come back. It saves the sign-in to
`~/.config/core-memory/chatgpt.json`, readable by you only, and shows the
account and whether you let it use your plan. Then point a binding at it:

```toml
[[binding]]
id = "plan"
provider = "openai-chatgpt"
endpoint = "https://api.openai.com/v1"   # the only endpoint it takes
model = "<a model from `core-memory login chatgpt --models`>"
max_output_tokens = 4096                 # reserved against a budget, never sent
# credentials_file = "chatgpt.json"      # beside the config; default above
```

and name it in `[reader]`, `[tool_reader]` or `[judge]`. The binding takes no
`temperature`, `seed` or `api_key_env`. `reasoning_effort` is sent.

```bash
core-memory login chatgpt --status    # who is signed in, plan and consent; sends nothing
core-memory login chatgpt --models    # the models your account may use
core-memory login chatgpt --consent   # sign in again and allow plan use, after declining it
core-memory logout chatgpt            # end the session with OpenAI and clear the tokens here
```

What this is bound by, from OpenAI's Sign in with ChatGPT terms:

- **Your own activity only.** Your plan may serve only requests that come from
  your own activity. Allow the binding's destination (`core-memory scope allow
  <scope> --binding plan`) only for scopes that hold your own conversations,
  notes and tool runs, never another person's. The plan is a destination of
  its own, `signed-in@api.openai.com`: a scope allowed for an `openai` API-key
  binding (`api.openai.com`) is not thereby allowed for your plan, so
  switching a reader to it sends nothing until you allow each scope again.
- **Your consent to background use.** Core Memory reads and judges in the
  background without asking each time, so `login` asks you first and records
  your answer and when you gave it. Without it, or on another machine than the
  one you gave it on, no call is made.
- **Tokens stay with you.** The sign-in is kept only in that file, on your
  machine, written whole and readable by you only; a file others can read is
  refused. Tokens are never logged, never put in a URL, and never in an error,
  a receipt or a work queue. They go only to OpenAI, at fixed addresses
  nothing in the config can change. Core Memory hands them to no other program
  and offers no API on top of them.
- **Your plan's limits apply.** Plus has a five-hour limit shared by every app
  that uses your plan. When it is reached, `core-memory read status` shows
  work put off with the problem `plan_limit_reached`, and no call of that
  kind is made for ten minutes. A call your plan turned away costs the work
  none of its attempts, so it waits for the limit to reset and is read then,
  with nothing for you to do. See and change what Core Memory may use at
  [ChatGPT settings, Usage](https://chatgpt.com/settings/usage). A sign-in
  that has ended shows as `signin_required`: run `core-memory login chatgpt`,
  then `core-memory read retry` for any work set aside meanwhile.

On a server with no browser, sign in where your browser is, into a file kept
apart from your own sign-in, copy it over SSH, and import it on the server.
The import keeps the server's own host ID and asks for your consent there:

```bash
# on your computer
core-memory login chatgpt --credentials ~/core-memory-server/chatgpt.json
scp ~/core-memory-server/chatgpt.json server:/tmp/chatgpt.json
rm -r ~/core-memory-server       # the server renews it from now on, not this computer
# on the server
core-memory login chatgpt --import /tmp/chatgpt.json && rm /tmp/chatgpt.json
```

Each renewal replaces the refresh token, so only one machine may renew a
sign-in: that is why the copy on your computer is deleted. Use a server only
you control.
Sign-in was built from OpenAI's documentation and tested against a stand-in
for OpenAI's servers; it has not yet been tried against OpenAI's own.

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
`read status` and `read queue` print one line for each kind of work.

## Judging how memories relate

A judge is a model that compares each new memory with the memories near it
in time, from every scope of the workspace that allows it. Add a judge. It
may use the same binding as the readers or one of its own: it reads more
text in one call than they do, and a wrong relationship does more harm than
a wrong type.

```toml
[judge]
binding = "small"
# window_ms = 604800000      # how far apart in time two memories may be: 7 days
# max_new_beads = 8          # new memories in one read
# max_window_beads = 40      # memories shown beside them
# second_read = true         # read a replacement twice before it is written
```

The judge needs each scope's consent to its destination, as a reader does,
and `core-memory read run`, `read status`, `read queue` and `serve` work for
it as they do for readers. A memory of a scope that has not allowed the
judge is not shown to it, and the others are still judged.

For each pair it compares, the judge answers with an arrow, or none, or that
it cannot tell. The arrows are those of Mark Burgess's Semantic Spacetime,
as his SSTorytime names them, each of one of his four types:

| Type | Arrows | Meaning |
|---|---|---|
| `leadsto` | `cause`, `result`, `enables`, `implem`, `repl`, `withdraws` | One follows from the other: it caused it, came of it, made it possible, carried it out, or, as a later statement, replaces or withdraws it. |
| `contains` | `same-memory`, `has-pt` | Pieces of one memory, in one message or across several; or one is an item of a whole. |
| `express` | `note`, `e.g.`, `i.e.`, `attr`, `def`, `source`, `solution`, `answers`, `confirms` | One states something of the other: a note, an example, an attribute, a definition, its source, an answer, a confirmation. |
| `near` | `see`, `sim`, `!contradicts`, `!confuse` | They are about one matter, say much the same, cannot both hold, or are easily confused and are not one. |

Each arrow but the symmetric ones has an inverse (`cause-by`, `repl-by`,
...), and the name the judge gives says which way it runs.
`docs/architecture.md` lists them all, with which are Burgess's names and
which this engine adds.

What it says is stored as IDs and fixed words, never as text:

- a **verdict** for every pair it was shown, whatever it said. "None" is a
  judgment and is kept. "Cannot tell", and a pair that was never shown, are
  not a no;
- an **edge** for each arrow, from one memory to the other. A memory is
  current until a later statement replaces or withdraws it, and it is
  replaced or withdrawn at most once, so a chain of statements has one
  current end and never branches. A contradiction ends nothing: both
  statements stay current, and the judge is shown both first while they do;
- a **group bead** for memories that are parts of one;
- a **name link** when two memories use different names for one thing, kept
  as the place of each name in its own text.

Code decides nothing here but what the judge is shown, and it goes by time
alone. It never proposes a pair or an edge.

A replacement or a withdrawal changes what a reader takes as current, so it
is not written on one reading. The two statements are read a second time,
alone, without the first answer, and the arrow is written only if the
second reading gives it again. Either way both verdicts are kept. Those
that share a statement are settled in the order they were given, so a chain
given in one reading is written from its oldest statement on. Turn this off
with `second_read = false`.

A memory with no other near it to be compared with is not sent: there is
no pair to read. Its pairs are read when memories near it arrive.

All of it is exported, as `verdict`, `bead`, `edge` and `name_link` records.
`export --scope` leaves out what joins that scope's memories to another
scope's.

## Recalling

```bash
core-memory recall launch date                    # every scope
core-memory recall '"launch date"' -- -draft      # a phrase, and not "draft"
core-memory recall budget --scope acme --follow 2 --include-ended
```

```bash
curl -s localhost:7600/v1/recall -H "Authorization: Bearer $TOKEN" \
    -d '{"query": "launch date", "scopes": ["acme"], "limit": 5}'
```

Recall finds the chunks whose text holds the words, with PostgreSQL's own
full-text search: words are compared as English stems, all are required,
`"quoted words"` stand together, `or` takes either and `-word` leaves out
what holds it. It makes no model call. The answer says `matched`,
`no_match`, or `no_terms` when the query held no word to look for: only
words too common to search for, or only words to leave out.

What comes back is what was stored, verbatim, chunk by chunk, with each
bead's event key, and what a judge said of it:

- **What is current.** A match that a later statement replaced or withdrew
  is answered by that later statement, marked as its successor. Ask with
  `include_ended` to see the old one too, marked `current: false`.
- **Disputes.** A bead is `disputed` while the judge says another current
  statement contradicts it.
- **Arrows.** Beads the judge related to the matches, up to `follow` steps
  away (1 unless asked, at most 2), and every arrow between the beads
  returned. Arrows never lead into a scope the caller may not read.

At most 50 matches (10 unless asked), 100 beads reached along arrows and
1 MiB of text come in one answer; a bead past the text limit comes without
its text, marked `text_omitted`.

Recall reads paused and revoked scopes too: they stop capture, not reading.
Erasing a message removes it from recall in the same transaction.

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
transaction. Every verdict a judge gave about it goes too, and every name
link to it. Its IDs stay as a tombstone, so links to it do not dangle and
the same message is refused if it is offered again. Edges to it stay, as two
IDs. A statement it had replaced does not become current again. Each erasure
is written to a journal with the reason and who asked, and exported as an
`erasure` record.

`erase scope` works in batches. Pause or revoke the scope first if nothing
more should be admitted to it while it is erased.

PostgreSQL keeps deleted rows' bytes in table pages until the space is
reused or the table is rewritten (`VACUUM` frees the space but does not
overwrite it; `VACUUM FULL` or `pg_repack` rewrites the table), erased keys
and words in index pages until `REINDEX`, and both in WAL and backups until
those expire. To remove erased text from disk, rewrite `chunk_text` and
reindex `events_key_idx` and `chunk_text_search_idx` after erasing, and
treat your backup retention as the longest that erased text can survive.

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
  given a grant and a policy. A verdict and a name link can be about
  memories of two scopes, so each carries both scopes on the row for a
  policy to decide by.
- **never writes** the engine's tables.

Two roles are created by the first migration:

| Role | May |
|---|---|
| `core_memory_engine` | Read and insert events, chunks, beads, receipts, reads and what a judge said. It cannot change or delete them, change a scope's state, give model consent, or erase. Give it to the engine's login role. |
| `core_memory_governor` | Call the governance functions. It reads no table. |

Both roles belong to the database cluster. If one cluster holds several Core
Memory databases with different operators, revoke `CONNECT` on each database
from the logins of the others.

Every row in `events`, `beads`, `erasures`, `chunk_reads`, `tool_outcomes`,
`verdicts`, `edges` and `name_links` has a `seq`. Within a workspace, rows
commit in `seq` order, so a reader that pages by
`workspace_id = $1 AND seq > $2` never misses a row.

### Reading in place

A system that shares the engine's database reads what the engine stored
where it is, and keeps no copy of it. A correction or an erasure is
therefore visible to it the moment the engine commits it, and there is
nothing to deliver, replay or reconcile. Two views save it from repeating
rules the engine owns:

| View | One row for | Columns |
|---|---|---|
| `core_memory.bead_state` | each live bead | `workspace_id`, `seq`, `bead_id`, `scope`, `kind`, `event_id`, `chunk_id`, `created_by`, `time_at`, `time_basis`; `current`; `ended_by` and `ended_with`, the bead whose arrow ended it and that arrow (`repl` or `withdraws`); `disputed`; `type_key`, the type its chunk was last read as |
| `core_memory.bead_text` | each chunk of a live bead's text | `workspace_id`, `bead_id`, `scope`, `ordinal`, `chunk_id`, `text` |

They apply recall's rules. A bead is current while no `repl` or `withdraws`
arrow points at it. A current bead is disputed while a `!contradicts` arrow
joins it to another live, current bead. To answer with what holds now,
follow `ended_by` from an ended bead until a current one: a chain never
branches, so it has one end.

Both views run with the rights of the role that reads them, so that role's
own grants and policies decide every row, and no view shows more than its
tables would. Nothing is granted on them. Grant a reading role the view and
the tables under it, and give it a policy on each table:

```sql
GRANT USAGE ON SCHEMA core_memory TO app_reader;
GRANT SELECT ON core_memory.bead_state, core_memory.bead_text,
                core_memory.beads, core_memory.edges, core_memory.chunk_reads,
                core_memory.chunks, core_memory.chunk_text
   TO app_reader;
-- What the role may read, decided by your own records of who may read what.
CREATE POLICY app_reader ON core_memory.beads FOR SELECT TO app_reader
    USING (app.may_read_scope(scope));
CREATE POLICY app_reader ON core_memory.chunk_text FOR SELECT TO app_reader
    USING (app.may_read_scope(scope));
CREATE POLICY app_reader ON core_memory.chunk_reads FOR SELECT TO app_reader
    USING (app.may_read_scope(scope));
CREATE POLICY app_reader ON core_memory.chunks FOR SELECT TO app_reader
    USING (true);   -- IDs, ranges and ordinals; the text is in chunk_text
CREATE POLICY app_reader ON core_memory.edges FOR SELECT TO app_reader
    USING (true);   -- IDs and a word from the arrows directory
```

With policies like these, a role sees the beads and text of the scopes it
may read, and nothing of the others. A dispute with a bead it may not see
is not shown as one. A policy that hides an edge hides what the edge says
too: a bead counts as ended only if the reader may see the edge that ended
it.

Revoking or pausing a scope stops what the engine takes in from it, not
what may be read of what it already holds. Who may read that is decided by
the reading system's own policies, as above. To make it unreadable for
everyone, erase it.

Govern from the same transactions that change your own records: revoke a
scope with `core_memory.set_scope_state`, erase with `core_memory.erase_*`,
and your change and the engine's commit together.

## Connecting to the database

The URL is read from `CORE_MEMORY_DATABASE_URL`, or from the variable named
by `[store] url_env`. It is never printed.

A connection to another machine always uses TLS and checks the server's
certificate and host name. A connection to this machine uses TLS whenever
the server offers it, and checks the certificate the same way: a server
whose installer gave it a self-signed certificate, as Ubuntu's
`pg_createcluster` does, is refused until `ca_file` names that certificate
or the URL says `sslmode=disable`. For a server whose certificate comes
from a private CA, such as Amazon RDS, set `ca_file`:

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

Upgrade the command (`uv tool upgrade core-memory`, or
`pipx upgrade core-memory`). Run `core-memory migrate` from the new build,
as the database role that owns the `core_memory` schema, then replace the
running engines. An engine writes only to the schema version it was built
for. After the migration, engines from the old build answer `busy` until
they are replaced, so nothing is written by code that does not know the new
schema.

2.0.0 released migrations 1 and 2, and a released migration never changes.
A database created by a build from before 2.0.0 must be recreated, not
migrated.

## Measured

Every result below was measured with live models: Claude Haiku 5.5 reading,
and Claude Opus 5.5 judging.

- **[Hard cases](../eval/adversarial/)** are 104 messages written to mislead
  a reader or a judge, each with the answers we count as right. Over three
  runs, the reader was right on 193 of 201 and the judge on 86 to 87 of 87.
  It closes a settled dispute, keeps an unsettled one open, and does not
  follow a message that tells it what to do. Three runs cost $0.40 and take
  3½ minutes.
- **[LoCoMo](../eval/locomo-smoke/)**: one 419-turn conversation is read and
  judged in about 6 minutes, for about $1.35. All ten conversations would
  cost about $17–19.
- **[Judges compared](../eval/compare/)**: Claude Sonnet 5.5 costs about 42%
  less as the judge. It missed replacements and groups that Opus got right,
  so Opus stays the judge.
