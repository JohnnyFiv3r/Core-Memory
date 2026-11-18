-- Core Memory engine schema, version 1.
--
-- The engine owns the core_memory schema. Only the engine writes its data
-- tables. Hosts govern through the functions at the end of this file and
-- read through grants and policies of their own. Nothing is granted to
-- PUBLIC, and every table has row security enabled.
--
-- The migration runner applies this file with search_path set to
-- pg_catalog, pg_temp. Every object here is named with its schema, and
-- every function pins the same search_path, so nothing an unprivileged
-- role creates in public or in a temporary schema is ever picked up.

-- Roles are cluster-wide. They may exist already, and two databases
-- migrating at once may both try to create them.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'core_memory_engine') THEN
        BEGIN
            CREATE ROLE core_memory_engine NOLOGIN;
        EXCEPTION WHEN duplicate_object OR unique_violation THEN
            NULL;
        END;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'core_memory_governor') THEN
        BEGIN
            CREATE ROLE core_memory_governor NOLOGIN;
        EXCEPTION WHEN duplicate_object OR unique_violation THEN
            NULL;
        END;
    END IF;
END
$$;

REVOKE ALL ON SCHEMA core_memory FROM PUBLIC;
GRANT USAGE ON SCHEMA core_memory TO core_memory_engine, core_memory_governor;
-- Both roles check the schema version before they do anything.
GRANT SELECT ON core_memory.schema_migrations TO core_memory_engine, core_memory_governor;

-- ---------------------------------------------------------------------------
-- Control plane
-- ---------------------------------------------------------------------------

-- A set of scopes whose beads may be judged together. id_key keys every ID
-- derived in the workspace. It never changes and is never shown to readers.
CREATE TABLE core_memory.workspaces (
    workspace_id text PRIMARY KEY
        CHECK (workspace_id <> '' AND octet_length(workspace_id) <= 512),
    id_key bytea NOT NULL CHECK (octet_length(id_key) = 32),
    created_at timestamptz NOT NULL
);

-- The workspace's change counter, and its write lock.
--
-- Every transaction that writes for a workspace locks this row first and
-- holds it until it ends, so a workspace has one writer at a time. Every
-- row the engine writes carries a seq taken from last_seq under that lock.
-- Rows therefore commit in seq order within a workspace, and a reader that
-- pages by `seq > last seen` misses nothing.
CREATE TABLE core_memory.workspace_seq (
    workspace_id text PRIMARY KEY REFERENCES core_memory.workspaces,
    last_seq bigint NOT NULL CHECK (last_seq >= 0)
) WITH (fillfactor = 50);

-- One connection to one source. A scope belongs to one workspace for good.
CREATE TABLE core_memory.scopes (
    scope text PRIMARY KEY CHECK (scope <> '' AND octet_length(scope) <= 512),
    workspace_id text NOT NULL REFERENCES core_memory.workspaces,
    state text NOT NULL CHECK (state IN ('active', 'paused', 'revoked')),
    created_at timestamptz NOT NULL,
    state_changed_at timestamptz NOT NULL
);

CREATE INDEX scopes_workspace_idx ON core_memory.scopes (workspace_id);

-- ---------------------------------------------------------------------------
-- Source: events, chunks and the one copy of their text
-- ---------------------------------------------------------------------------

CREATE TABLE core_memory.events (
    event_id uuid PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES core_memory.workspaces,
    scope text NOT NULL REFERENCES core_memory.scopes,
    state text NOT NULL CHECK (state IN ('live', 'erased')),
    -- The workspace's change counter when the event was admitted.
    seq bigint NOT NULL CHECK (seq > 0),
    -- What the source declared. NULL once the event is erased.
    source_ns text,
    external_id text,
    revision text,
    engine_assigned_revision boolean,
    event_hash bytea CHECK (octet_length(event_hash) = 32),
    content_hash bytea CHECK (octet_length(content_hash) = 32),
    info jsonb CHECK (jsonb_typeof(info) = 'object'),
    -- Engine-side facts. They stay after an erase.
    profile_id text NOT NULL,
    profile_rev integer NOT NULL CHECK (profile_rev > 0),
    chunker_id text NOT NULL,
    chunker_rev integer NOT NULL CHECK (chunker_rev > 0),
    chunker_config_hash bytea NOT NULL CHECK (octet_length(chunker_config_hash) = 32),
    chunk_count integer NOT NULL CHECK (chunk_count > 0),
    -- The time the event is ordered by, and where it came from.
    time_at timestamptz NOT NULL,
    time_basis text NOT NULL CHECK (time_basis IN ('source', 'source_bucket', 'captured')),
    captured_at timestamptz NOT NULL,
    erased_at timestamptz,
    CHECK (
        (state = 'live'
            AND source_ns IS NOT NULL AND external_id IS NOT NULL
            AND engine_assigned_revision IS NOT NULL
            AND event_hash IS NOT NULL AND content_hash IS NOT NULL
            AND info IS NOT NULL AND erased_at IS NULL)
        OR (state = 'erased'
            AND source_ns IS NULL AND external_id IS NULL AND revision IS NULL
            AND engine_assigned_revision IS NULL
            AND event_hash IS NULL AND content_hash IS NULL
            AND info IS NULL AND erased_at IS NOT NULL
            AND time_basis = 'captured' AND time_at = captured_at)
    ),
    UNIQUE (workspace_id, seq)
);

-- Finds every revision of one source object.
CREATE INDEX events_key_idx
    ON core_memory.events (scope, source_ns, external_id) WHERE state = 'live';
CREATE INDEX events_scope_order_idx ON core_memory.events (scope, seq);

-- A byte range of an event's content. No text.
CREATE TABLE core_memory.chunks (
    chunk_id uuid PRIMARY KEY,
    event_id uuid NOT NULL REFERENCES core_memory.events,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    byte_start bigint NOT NULL CHECK (byte_start >= 0),
    byte_end bigint NOT NULL CHECK (byte_end > byte_start),
    char_start bigint NOT NULL CHECK (char_start >= 0),
    char_end bigint NOT NULL CHECK (char_end > char_start),
    UNIQUE (event_id, ordinal)
);

-- The only copy of source text, with the facets computed from it. Erasing
-- an event deletes its rows here.
CREATE TABLE core_memory.chunk_text (
    chunk_id uuid PRIMARY KEY REFERENCES core_memory.chunks,
    scope text NOT NULL REFERENCES core_memory.scopes,
    text text NOT NULL CHECK (text <> ''),
    content_hash bytea NOT NULL
        CHECK (content_hash = pg_catalog.sha256(pg_catalog.convert_to(text, 'UTF8'))),
    facets jsonb NOT NULL CHECK (jsonb_typeof(facets) = 'object')
);

CREATE INDEX chunk_text_scope_idx ON core_memory.chunk_text (scope);

-- ANALYZE copies sample values of a column into pg_statistic, where an
-- erase cannot reach them. Columns that hold what a source said, or a hash
-- of it, are therefore left out of the planner's statistics. That includes
-- time_at, which is the source's own time until the event is erased. Every
-- query on these columns goes through a primary key, events_key_idx or
-- beads_window_idx, none of which needs the statistics to be chosen.
ALTER TABLE core_memory.events
    ALTER COLUMN source_ns SET STATISTICS 0,
    ALTER COLUMN external_id SET STATISTICS 0,
    ALTER COLUMN revision SET STATISTICS 0,
    ALTER COLUMN event_hash SET STATISTICS 0,
    ALTER COLUMN content_hash SET STATISTICS 0,
    ALTER COLUMN info SET STATISTICS 0,
    ALTER COLUMN time_at SET STATISTICS 0;
ALTER TABLE core_memory.chunk_text
    ALTER COLUMN text SET STATISTICS 0,
    ALTER COLUMN content_hash SET STATISTICS 0,
    ALTER COLUMN facets SET STATISTICS 0;

-- ---------------------------------------------------------------------------
-- Beads: unchanging pointers that other records link to
-- ---------------------------------------------------------------------------

CREATE TABLE core_memory.beads (
    bead_id uuid PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES core_memory.workspaces,
    -- The scope and the event of a bead that stands for source content.
    -- A bead a judge made belongs to the workspace and has neither.
    scope text REFERENCES core_memory.scopes,
    kind text NOT NULL CHECK (kind IN ('chunk', 'tool', 'label', 'group')),
    state text NOT NULL CHECK (state IN ('live', 'erased')),
    event_id uuid REFERENCES core_memory.events,
    chunk_id uuid REFERENCES core_memory.chunks,
    created_by text NOT NULL CHECK (created_by IN ('engine', 'judge')),
    -- The judge's call that made the bead. A bead of source content has
    -- none.
    call_id uuid,
    time_at timestamptz NOT NULL,
    time_basis text NOT NULL CHECK (time_basis IN ('source', 'source_bucket', 'captured')),
    created_at timestamptz NOT NULL,
    seq bigint NOT NULL CHECK (seq > 0),
    -- A message chunk's bead has the chunk's own ID. A tool call is one
    -- bead, with its event's ID, however many chunks its text is cut into.
    CHECK ((kind = 'chunk') = (chunk_id IS NOT NULL AND bead_id = chunk_id)),
    CHECK (kind <> 'tool' OR (bead_id = event_id AND chunk_id IS NULL)),
    CHECK ((kind IN ('chunk', 'tool')) = (scope IS NOT NULL AND event_id IS NOT NULL)),
    CHECK ((kind IN ('chunk', 'tool')) = (created_by = 'engine')),
    CHECK ((created_by = 'judge') = (call_id IS NOT NULL)),
    UNIQUE (workspace_id, seq)
);

CREATE INDEX beads_window_idx ON core_memory.beads (workspace_id, time_at, seq);
CREATE INDEX beads_event_idx ON core_memory.beads (event_id);

-- The source's own time, as on events.
ALTER TABLE core_memory.beads ALTER COLUMN time_at SET STATISTICS 0;

-- ---------------------------------------------------------------------------
-- Reader checkpoints, profile pins and the erasure journal
-- ---------------------------------------------------------------------------

CREATE TABLE core_memory.checkpoints (
    scope text NOT NULL REFERENCES core_memory.scopes,
    checkpoint_key text NOT NULL
        CHECK (checkpoint_key <> '' AND octet_length(checkpoint_key) <= 128),
    sequence bigint NOT NULL CHECK (sequence > 0),
    hash bytea NOT NULL CHECK (octet_length(hash) = 32),
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (scope, checkpoint_key)
);

-- A reader's hash of its own position, which may be a hash of what it
-- last read. Kept out of the planner's statistics like the hashes above.
ALTER TABLE core_memory.checkpoints ALTER COLUMN hash SET STATISTICS 0;

CREATE TABLE core_memory.pins (
    kind text NOT NULL CHECK (kind IN ('source_profile', 'chunker')),
    id text NOT NULL,
    rev integer NOT NULL CHECK (rev > 0),
    hash bytea NOT NULL CHECK (octet_length(hash) = 32),
    pinned_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
    PRIMARY KEY (kind, id, rev)
);

-- One row per erased event. It holds no content. After a backup is
-- restored, replaying this journal erases again what was erased before.
CREATE TABLE core_memory.erasures (
    workspace_id text NOT NULL REFERENCES core_memory.workspaces,
    seq bigint NOT NULL CHECK (seq > 0),
    event_id uuid NOT NULL UNIQUE REFERENCES core_memory.events,
    scope text NOT NULL,
    reason text NOT NULL CHECK (btrim(reason) <> '' AND octet_length(reason) <= 256),
    actor text NOT NULL CHECK (btrim(actor) <> '' AND octet_length(actor) <= 256),
    chunks_erased integer NOT NULL CHECK (chunks_erased >= 0),
    erased_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, seq)
);

-- ---------------------------------------------------------------------------
-- Model calls: consent, receipts and results
-- ---------------------------------------------------------------------------

-- Where a scope's text may be sent. A destination is the host, and port if
-- any, of a model endpoint. No row, or a revoked row, means no call.
CREATE TABLE core_memory.scope_destinations (
    scope text NOT NULL REFERENCES core_memory.scopes,
    destination text NOT NULL
        CHECK (destination <> '' AND octet_length(destination) <= 255),
    state text NOT NULL CHECK (state IN ('granted', 'revoked')),
    granted_at timestamptz NOT NULL,
    revoked_at timestamptz,
    PRIMARY KEY (scope, destination),
    CHECK ((state = 'revoked') = (revoked_at IS NOT NULL))
);

-- The receipt for one model call. It is written and committed before
-- anything is sent, in the transaction that checks consent and reads the
-- text. It names the destination and the IDs of what was sent. No text.
CREATE TABLE core_memory.outbound_calls (
    call_id uuid PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES core_memory.workspaces,
    seq bigint NOT NULL CHECK (seq > 0),
    purpose text NOT NULL CHECK (purpose IN ('chunk_read', 'tool_read', 'judge', 'second_read')),
    -- The one scope whose text a read sends. A judge is shown beads of
    -- every scope of the workspace that allows it, so its calls name none.
    scope text REFERENCES core_memory.scopes,
    destination text NOT NULL,
    binding_id text NOT NULL,
    binding_rev bytea NOT NULL CHECK (octet_length(binding_rev) = 32),
    model text NOT NULL,
    -- 1 for a first call, 2 for the one repair of a rejected reply.
    attempt integer NOT NULL CHECK (attempt IN (1, 2)),
    repairs uuid REFERENCES core_memory.outbound_calls,
    -- The chunks whose text was sent.
    chunk_ids uuid[] NOT NULL,
    -- For a judge's call, every bead it was shown. A tool call's bead is
    -- shown as what a model wrote about it, so it has no chunk above.
    bead_ids uuid[],
    bytes_sent bigint NOT NULL CHECK (bytes_sent > 0),
    -- The most tokens the call may use, counted against the budget whether
    -- or not it uses them.
    reserved_tokens bigint NOT NULL CHECK (reserved_tokens >= 0),
    permitted_at timestamptz NOT NULL,
    UNIQUE (workspace_id, seq),
    CHECK ((attempt = 2) = (repairs IS NOT NULL)),
    CHECK ((purpose IN ('chunk_read', 'tool_read')) = (scope IS NOT NULL)),
    CHECK ((purpose IN ('chunk_read', 'tool_read')) = (bead_ids IS NULL)),
    CHECK (cardinality(chunk_ids) > 0 OR cardinality(bead_ids) > 0)
);

CREATE INDEX outbound_calls_budget_idx
    ON core_memory.outbound_calls (workspace_id, binding_id, permitted_at);

-- How a call ended. A call with no row here was permitted and its outcome
-- was never recorded: its text may have been sent.
CREATE TABLE core_memory.outbound_results (
    call_id uuid PRIMARY KEY REFERENCES core_memory.outbound_calls,
    result text NOT NULL CHECK (result IN (
        'accepted',   -- the reply was valid
        'rejected',   -- the reply was not valid
        'refused',    -- the model declined, or the request or reply did not fit
        'not_sent',   -- the provider did not process the request
        'in_doubt',   -- the request may have been processed
        'failed'      -- the provider turned the request itself down
    )),
    input_tokens bigint CHECK (input_tokens >= 0),
    output_tokens bigint CHECK (output_tokens >= 0),
    finished_at timestamptz NOT NULL
);

-- ---------------------------------------------------------------------------
-- What a model read in a chunk
-- ---------------------------------------------------------------------------

-- One model read of one chunk: its type, or why the model gave none. Rows
-- are added, never changed. The row with the highest seq for a chunk is the
-- current one. Erasing the chunk's event deletes its rows here.
CREATE TABLE core_memory.chunk_reads (
    workspace_id text NOT NULL REFERENCES core_memory.workspaces,
    seq bigint NOT NULL CHECK (seq > 0),
    chunk_id uuid NOT NULL REFERENCES core_memory.chunks,
    scope text NOT NULL REFERENCES core_memory.scopes,
    call_id uuid NOT NULL REFERENCES core_memory.outbound_calls,
    status text NOT NULL CHECK (status IN ('typed', 'abstained')),
    type_key text,
    type_rev integer CHECK (type_rev > 0),
    abstained text CHECK (abstained IN ('no_fit', 'insufficient_evidence', 'ambiguous')),
    vocabulary_id text NOT NULL,
    vocabulary_hash bytea NOT NULL CHECK (octet_length(vocabulary_hash) = 32),
    binding_id text NOT NULL,
    binding_rev bytea NOT NULL CHECK (octet_length(binding_rev) = 32),
    model text NOT NULL,
    prompt_rev integer NOT NULL CHECK (prompt_rev > 0),
    -- Spans the model offered that were not found in the chunk, and so
    -- were not stored.
    spans_rejected integer NOT NULL CHECK (spans_rejected >= 0),
    read_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, seq),
    CHECK ((status = 'typed') = (type_key IS NOT NULL AND type_rev IS NOT NULL)),
    CHECK ((status = 'abstained') = (abstained IS NOT NULL))
);

CREATE INDEX chunk_reads_chunk_idx ON core_memory.chunk_reads (chunk_id, seq);

-- An entity, action or outcome a read found: a byte range of the chunk's
-- text, counted from the start of the chunk. No text is copied.
CREATE TABLE core_memory.chunk_read_spans (
    workspace_id text NOT NULL,
    read_seq bigint NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    chunk_id uuid NOT NULL REFERENCES core_memory.chunks,
    kind text NOT NULL CHECK (kind IN ('entity', 'action', 'outcome')),
    entity_kind text CHECK (entity_kind IN (
        'person', 'organization', 'place', 'product', 'project', 'artifact', 'event', 'other'
    )),
    byte_start bigint NOT NULL CHECK (byte_start >= 0),
    byte_end bigint NOT NULL CHECK (byte_end > byte_start),
    PRIMARY KEY (workspace_id, read_seq, ordinal),
    FOREIGN KEY (workspace_id, read_seq) REFERENCES core_memory.chunk_reads,
    CHECK ((kind = 'entity') = (entity_kind IS NOT NULL))
);

CREATE INDEX chunk_read_spans_chunk_idx ON core_memory.chunk_read_spans (chunk_id);

-- ---------------------------------------------------------------------------
-- What a model read in a tool call
-- ---------------------------------------------------------------------------

-- One model read of one tool call: what the call did, what came of it, and
-- how it ended. A model never sees a tool call's stored text whole: code
-- shortens it first, by the rules the compressor columns name.
--
-- action and outcome are the one place the engine stores text a model
-- wrote. Later readers are given this record in place of the tool's own
-- text. Rows are added, never changed; the row with the highest seq for
-- an event is the current one. Erasing the event deletes its rows here.
CREATE TABLE core_memory.tool_outcomes (
    workspace_id text NOT NULL REFERENCES core_memory.workspaces,
    seq bigint NOT NULL CHECK (seq > 0),
    event_id uuid NOT NULL REFERENCES core_memory.events,
    scope text NOT NULL REFERENCES core_memory.scopes,
    call_id uuid NOT NULL REFERENCES core_memory.outbound_calls,
    status text NOT NULL CHECK (status IN ('succeeded', 'failed', 'partial', 'unknown')),
    action text NOT NULL CHECK (btrim(action) <> '' AND char_length(action) <= 120),
    outcome text NOT NULL CHECK (btrim(outcome) <> '' AND char_length(outcome) <= 200),
    compressor_id text NOT NULL,
    compressor_rev integer NOT NULL CHECK (compressor_rev > 0),
    -- Bytes of the shortened text the model was shown.
    compressed_bytes bigint NOT NULL CHECK (compressed_bytes >= 0),
    binding_id text NOT NULL,
    binding_rev bytea NOT NULL CHECK (octet_length(binding_rev) = 32),
    model text NOT NULL,
    prompt_rev integer NOT NULL CHECK (prompt_rev > 0),
    read_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, seq)
);

CREATE INDEX tool_outcomes_event_idx ON core_memory.tool_outcomes (event_id, seq);

-- What a model wrote about what a source said, kept out of the planner's
-- statistics like the source's own text.
ALTER TABLE core_memory.tool_outcomes
    ALTER COLUMN action SET STATISTICS 0,
    ALTER COLUMN outcome SET STATISTICS 0;

-- ---------------------------------------------------------------------------
-- What a judge said about how beads relate
--
-- Code computes what a judge is shown and records what it says. Code never
-- proposes an edge and never picks a pair. None of these tables holds text.
-- ---------------------------------------------------------------------------

-- One row for each read of a judge whose reply was stored: the beads it
-- was shown, by ID. A bead is new in at most one read.
CREATE TABLE core_memory.judge_reads (
    call_id uuid PRIMARY KEY REFERENCES core_memory.outbound_calls,
    workspace_id text NOT NULL REFERENCES core_memory.workspaces,
    seq bigint NOT NULL CHECK (seq > 0),
    -- 2 for the second, blind read of one proposed label.
    read_no integer NOT NULL CHECK (read_no IN (1, 2)),
    new_beads uuid[] NOT NULL CHECK (cardinality(new_beads) > 0),
    window_beads uuid[] NOT NULL,
    rubric_rev integer NOT NULL CHECK (rubric_rev > 0),
    binding_id text NOT NULL,
    binding_rev bytea NOT NULL CHECK (octet_length(binding_rev) = 32),
    model text NOT NULL,
    judged_at timestamptz NOT NULL,
    UNIQUE (workspace_id, seq)
);

CREATE INDEX judge_reads_new_idx ON core_memory.judge_reads USING gin (new_beads);

ALTER TABLE core_memory.beads
    ADD CONSTRAINT beads_call_id_fkey FOREIGN KEY (call_id) REFERENCES core_memory.judge_reads;

-- One row for every pair a judge was shown, whatever it said. A host may
-- learn from it which pairs are worth showing, so it records a no as
-- carefully as a yes: 'unsure', and a pair never shown, are not a no.
--
-- The two beads are in time order, and the direction is explicit because
-- the order in which messages arrived is not the order in which things
-- happened. columns holds what code computed about the pair at that
-- moment: numbers, flags and vocabulary keys. A row is deleted when either
-- of its beads is erased.
CREATE TABLE core_memory.verdicts (
    workspace_id text NOT NULL REFERENCES core_memory.workspaces,
    seq bigint NOT NULL CHECK (seq > 0),
    earlier_bead uuid NOT NULL REFERENCES core_memory.beads,
    later_bead uuid NOT NULL REFERENCES core_memory.beads,
    -- The scope of each bead. A judge reads across the scopes of a
    -- workspace, and columns is computed from what both beads say, so who
    -- may read a verdict depends on both scopes. They are on the row so
    -- that a host's row security can decide from the row alone. They are
    -- read from the beads when the row is stored, whatever the statement
    -- gave.
    earlier_scope text NOT NULL REFERENCES core_memory.scopes,
    later_scope text NOT NULL REFERENCES core_memory.scopes,
    verdict text NOT NULL CHECK (verdict IN (
        'near', 'leadsto', 'contains', 'express', 'supersedes', 'none', 'unsure'
    )),
    -- From which bead to which the relationship runs.
    direction text NOT NULL CHECK (direction IN (
        'earlier_to_later', 'later_to_earlier', 'undirected'
    )),
    label text CHECK (label IN ('supersede', 'retract', 'reaffirm', 'conflict')),
    -- Why a supersedes verdict wrote no label. Without a note, its label
    -- was written by the same read.
    --   not_current: one of its beads was no longer current when the read
    --     was stored.
    --   label_taken: its source already carried a label it could not join.
    --   awaits_second_read: the label is held until a second read agrees.
    --   second_read_differs: this second read did not agree with the first.
    note text CHECK (note IN (
        'not_current', 'label_taken', 'awaits_second_read', 'second_read_differs'
    )),
    call_id uuid NOT NULL REFERENCES core_memory.judge_reads,
    columns jsonb NOT NULL,
    columns_rev integer NOT NULL CHECK (columns_rev > 0),
    PRIMARY KEY (workspace_id, seq),
    CHECK (earlier_bead <> later_bead),
    CHECK ((verdict = 'supersedes') = (label IS NOT NULL)),
    CHECK (verdict NOT IN ('near', 'contains', 'none', 'unsure') OR direction = 'undirected'),
    CHECK (verdict NOT IN ('leadsto', 'express', 'supersedes') OR direction <> 'undirected'),
    CHECK (note IS NULL OR verdict = 'supersedes')
);

CREATE INDEX verdicts_earlier_idx ON core_memory.verdicts (earlier_bead);
CREATE INDEX verdicts_later_idx ON core_memory.verdicts (later_bead);

-- Computed from what the two beads say.
ALTER TABLE core_memory.verdicts ALTER COLUMN columns SET STATISTICS 0;

-- A relationship between two beads. It has no state and no weight, and is
-- never changed or deleted: erasing a bead leaves its edges, which hold
-- IDs and say only that there was a link.
--
-- Every edge points at a bead of source content. A supersedes edge runs
-- from the label bead of a later statement to the bead of the earlier
-- statement it follows, and a contains edge from a group bead to a member.
-- The others run from one bead of source content to another. A near edge
-- has no direction, and is stored from the smaller ID to the larger.
CREATE TABLE core_memory.edges (
    workspace_id text NOT NULL REFERENCES core_memory.workspaces,
    seq bigint NOT NULL CHECK (seq > 0),
    edge_type text NOT NULL CHECK (edge_type IN (
        'near', 'leadsto', 'contains', 'express', 'supersedes'
    )),
    from_bead uuid NOT NULL REFERENCES core_memory.beads,
    to_bead uuid NOT NULL REFERENCES core_memory.beads,
    call_id uuid NOT NULL REFERENCES core_memory.judge_reads,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, seq),
    UNIQUE (edge_type, from_bead, to_bead),
    CHECK (from_bead <> to_bead),
    CHECK (edge_type <> 'near' OR from_bead < to_bead)
);

CREATE INDEX edges_to_idx ON core_memory.edges (to_bead, edge_type);
-- A chain never branches: a bead is superseded at most once. A bead of
-- source content is current while no supersedes edge points at it.
CREATE UNIQUE INDEX edges_superseded_once_idx ON core_memory.edges (to_bead)
    WHERE edge_type = 'supersedes';

-- What a label bead says. It stands for its source, the bead of the later
-- statement, and there is at most one label for a source.
CREATE TABLE core_memory.labels (
    bead_id uuid PRIMARY KEY REFERENCES core_memory.beads,
    workspace_id text NOT NULL REFERENCES core_memory.workspaces,
    label text NOT NULL CHECK (label IN ('supersede', 'retract', 'reaffirm', 'conflict')),
    source_bead uuid NOT NULL UNIQUE REFERENCES core_memory.beads,
    call_id uuid NOT NULL REFERENCES core_memory.judge_reads,
    CHECK (bead_id <> source_bead)
);

-- The conflicts of a workspace, which a judge is shown first while they
-- are open.
CREATE INDEX labels_conflict_idx ON core_memory.labels (workspace_id)
    WHERE label = 'conflict';

-- A label a judge proposed that is not written until a second read of the
-- same pair agrees with it. IDs only. It goes when the second read is
-- stored, when it can no longer be written, or when its source or one of
-- its heads is erased.
--
-- Labels that share a bead are read a second time in the order they were
-- held, which is the order of `ordinal`, a number from the workspace's
-- counter. A chain given in one read is then written from its oldest
-- statement on, and no label makes another unwritable before its second
-- read.
CREATE TABLE core_memory.pending_labels (
    source_bead uuid PRIMARY KEY REFERENCES core_memory.beads,
    workspace_id text NOT NULL REFERENCES core_memory.workspaces,
    label text NOT NULL CHECK (label IN ('supersede', 'retract', 'reaffirm', 'conflict')),
    -- The beads shown to the judge as the heads: one or two.
    head_beads uuid[] NOT NULL CHECK (cardinality(head_beads) BETWEEN 1 AND 2),
    ordinal bigint NOT NULL CHECK (ordinal > 0),
    call_id uuid NOT NULL REFERENCES core_memory.judge_reads,
    proposed_at timestamptz NOT NULL,
    UNIQUE (workspace_id, ordinal)
);

-- Two names for one entity, each as the byte range where it stands in its
-- own chunk. No text is copied. Deleted when either chunk's event is
-- erased. A link has no direction: its ends are in the order of their
-- beads' IDs, so the same two names linked again, by any judge, are one
-- row.
CREATE TABLE core_memory.name_links (
    workspace_id text NOT NULL REFERENCES core_memory.workspaces,
    seq bigint NOT NULL CHECK (seq > 0),
    a_bead uuid NOT NULL REFERENCES core_memory.beads,
    -- The scope of each bead, as on a verdict: a link says where a name
    -- stands in each of two chunks, and who may read it depends on both.
    a_scope text NOT NULL REFERENCES core_memory.scopes,
    a_start bigint NOT NULL CHECK (a_start >= 0),
    a_end bigint NOT NULL,
    b_bead uuid NOT NULL REFERENCES core_memory.beads,
    b_scope text NOT NULL REFERENCES core_memory.scopes,
    b_start bigint NOT NULL CHECK (b_start >= 0),
    b_end bigint NOT NULL,
    call_id uuid NOT NULL REFERENCES core_memory.judge_reads,
    linked_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, seq),
    UNIQUE (a_bead, a_start, a_end, b_bead, b_start, b_end),
    CHECK (a_end > a_start AND b_end > b_start),
    CHECK (a_bead < b_bead)
);

CREATE INDEX name_links_b_idx ON core_memory.name_links (b_bead);

-- ---------------------------------------------------------------------------
-- Work the engine still owes. No text.
-- ---------------------------------------------------------------------------

CREATE TABLE core_memory.work (
    kind text NOT NULL CHECK (kind IN ('chunk_read', 'tool_read', 'judge', 'second_read')),
    -- What is to be read: a chunk for chunk_read, an event for tool_read,
    -- a new bead for judge, and the source of a proposed label for
    -- second_read.
    item_id uuid NOT NULL,
    -- The event the item belongs to. Erasing it deletes the work.
    event_id uuid NOT NULL REFERENCES core_memory.events,
    workspace_id text NOT NULL REFERENCES core_memory.workspaces,
    scope text NOT NULL REFERENCES core_memory.scopes,
    state text NOT NULL CHECK (state IN ('ready', 'leased', 'failed')),
    -- Times the item was handed to a worker.
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    -- True once the item was part of a call that failed as a whole. From
    -- then on it is sent on its own, so it cannot hold others back.
    alone boolean NOT NULL DEFAULT false,
    not_before timestamptz NOT NULL,
    leased_until timestamptz,
    -- A short code for the last problem. Never model or source text.
    problem text CHECK (octet_length(problem) <= 64),
    enqueued_at timestamptz NOT NULL,
    PRIMARY KEY (kind, item_id),
    CHECK ((state = 'leased') = (leased_until IS NOT NULL)),
    CHECK (kind <> 'tool_read' OR item_id = event_id)
);

CREATE INDEX work_next_idx ON core_memory.work (kind, not_before) WHERE state <> 'failed';
-- One judge reads in a workspace at a time. This finds the one.
CREATE INDEX work_judging_idx ON core_memory.work (workspace_id)
    WHERE state = 'leased' AND kind IN ('judge', 'second_read');
CREATE INDEX work_scope_idx ON core_memory.work (scope);
CREATE INDEX work_event_idx ON core_memory.work (event_id);

-- ---------------------------------------------------------------------------
-- Immutability
--
-- These triggers stop mistakes, including the table owner's. They are not
-- a defence against the owner, who can drop them.
-- ---------------------------------------------------------------------------

CREATE FUNCTION core_memory.refuse_change() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    RAISE EXCEPTION 'core_memory.% rows cannot be %', TG_TABLE_NAME,
        CASE TG_OP WHEN 'UPDATE' THEN 'changed' WHEN 'DELETE' THEN 'deleted' ELSE 'truncated' END
        USING ERRCODE = 'restrict_violation';
END
$$;

-- An event changes once: from live to erased, losing what its source
-- declared and nothing else. Only the erase functions turn
-- core_memory.erasing on, and only for the statements that erase.
CREATE FUNCTION core_memory.events_only_erase() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF current_setting('core_memory.erasing', true) = 'on'
        AND OLD.state = 'live' AND NEW.state = 'erased'
        AND NEW.event_id = OLD.event_id
        AND NEW.workspace_id = OLD.workspace_id
        AND NEW.scope = OLD.scope
        AND NEW.seq = OLD.seq
        AND NEW.profile_id = OLD.profile_id
        AND NEW.profile_rev = OLD.profile_rev
        AND NEW.chunker_id = OLD.chunker_id
        AND NEW.chunker_rev = OLD.chunker_rev
        AND NEW.chunker_config_hash = OLD.chunker_config_hash
        AND NEW.chunk_count = OLD.chunk_count
        AND NEW.captured_at = OLD.captured_at
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'core_memory.events rows cannot be changed'
        USING ERRCODE = 'restrict_violation';
END
$$;

-- A bead changes once: from live to erased, ordered from then on by when
-- it was created.
CREATE FUNCTION core_memory.beads_only_erase() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF current_setting('core_memory.erasing', true) = 'on'
        AND OLD.state = 'live' AND NEW.state = 'erased'
        AND NEW.time_basis = 'captured' AND NEW.time_at = OLD.created_at
        AND NEW.bead_id = OLD.bead_id
        AND NEW.workspace_id = OLD.workspace_id
        AND NEW.scope IS NOT DISTINCT FROM OLD.scope
        AND NEW.kind = OLD.kind
        AND NEW.event_id IS NOT DISTINCT FROM OLD.event_id
        AND NEW.chunk_id IS NOT DISTINCT FROM OLD.chunk_id
        AND NEW.created_by = OLD.created_by
        AND NEW.call_id IS NOT DISTINCT FROM OLD.call_id
        AND NEW.created_at = OLD.created_at
        AND NEW.seq = OLD.seq
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'core_memory.beads rows cannot be changed'
        USING ERRCODE = 'restrict_violation';
END
$$;

-- A chunk's text, and what was read in it, are deleted only for an event
-- that has just been erased. For any table with a chunk_id column.
CREATE FUNCTION core_memory.deleted_only_with_its_event() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF current_setting('core_memory.erasing', true) = 'on' AND EXISTS (
        SELECT 1
          FROM core_memory.chunks AS c
          JOIN core_memory.events AS e ON e.event_id = c.event_id
         WHERE c.chunk_id = OLD.chunk_id AND e.state = 'erased'
    ) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'core_memory.% rows cannot be deleted', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END
$$;

-- The same for what was read in a tool call. For any table with an
-- event_id column.
CREATE FUNCTION core_memory.deleted_only_when_its_event_is_erased() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF current_setting('core_memory.erasing', true) = 'on' AND EXISTS (
        SELECT 1 FROM core_memory.events AS e
         WHERE e.event_id = OLD.event_id AND e.state = 'erased'
    ) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'core_memory.% rows cannot be deleted', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END
$$;

-- What a model read in a chunk is stored only while the chunk's event is
-- live. The engine checks that where it stores a read, under the workspace
-- lock that an erasure also takes. This stops a statement that does not:
-- a row about an erased event would be out of reach of every erase.
CREATE FUNCTION core_memory.stored_only_with_a_live_event() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM core_memory.chunks AS c
          JOIN core_memory.events AS e ON e.event_id = c.event_id
         WHERE c.chunk_id = NEW.chunk_id AND e.state = 'live'
    ) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'core_memory.% takes rows only for an event that is live', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END
$$;

-- The same for what was read in a tool call. For any table with an
-- event_id column.
CREATE FUNCTION core_memory.stored_only_when_its_event_is_live() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM core_memory.events AS e
         WHERE e.event_id = NEW.event_id AND e.state = 'live'
    ) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'core_memory.% takes rows only for an event that is live', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END
$$;

-- What a judge said about two beads is deleted only when one of them has
-- just been erased. The trigger names the table's two bead columns.
CREATE FUNCTION core_memory.deleted_only_when_a_bead_is_erased() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    gone jsonb := to_jsonb(OLD);
BEGIN
    IF current_setting('core_memory.erasing', true) = 'on' AND EXISTS (
        SELECT 1 FROM core_memory.beads AS b
         WHERE b.bead_id IN ((gone ->> TG_ARGV[0])::uuid, (gone ->> TG_ARGV[1])::uuid)
           AND b.state = 'erased'
    ) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'core_memory.% rows cannot be deleted', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END
$$;

-- What a judge says is stored only about beads that are live. The engine
-- checks that where it stores a judgment, under the workspace lock that an
-- erasure also takes. This stops a statement that does not: a verdict or a
-- name link about an erased bead would be out of reach of every erase. The
-- trigger names the table's bead columns.
CREATE FUNCTION core_memory.stored_only_for_live_beads() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    added jsonb := to_jsonb(NEW);
    named text;
    ids jsonb;
BEGIN
    FOREACH named IN ARRAY TG_ARGV LOOP
        -- A column holds one bead, or an array of them.
        ids := CASE jsonb_typeof(added -> named)
                   WHEN 'array' THEN added -> named
                   ELSE jsonb_build_array(added -> named)
               END;
        IF jsonb_array_length(ids) = 0 OR EXISTS (
            SELECT 1 FROM jsonb_array_elements_text(ids) AS i (id)
             WHERE NOT EXISTS (
                 SELECT 1 FROM core_memory.beads AS b
                  WHERE b.bead_id = i.id::uuid AND b.state = 'live'
                    AND b.workspace_id = added ->> 'workspace_id')
        ) THEN
            RAISE EXCEPTION 'core_memory.% takes rows only about beads that are live',
                TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation',
                      DETAIL = 'Each bead must be live and of the row''s own workspace.';
        END IF;
    END LOOP;
    RETURN NEW;
END
$$;

-- A name link says where a name stands in each of two chunks. Each range
-- lies inside its chunk's text, and a bead with no text of its own, as a
-- tool call's has none, has no name to link.
CREATE FUNCTION core_memory.name_links_within_their_chunks() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM core_memory.chunk_text AS t
         WHERE t.chunk_id = NEW.a_bead AND NEW.a_end <= octet_length(t.text))
       OR NOT EXISTS (
        SELECT 1 FROM core_memory.chunk_text AS t
         WHERE t.chunk_id = NEW.b_bead AND NEW.b_end <= octet_length(t.text))
    THEN
        RAISE EXCEPTION 'a name link names a range inside the text of each of its chunks'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

-- A verdict and a name link carry the scope of each of their beads. The
-- scopes are read from the beads here, so the two cannot disagree, whoever
-- writes the row. A bead a judge made has no scope, and a row about one is
-- refused by the column.
CREATE FUNCTION core_memory.verdicts_carry_scopes() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    NEW.earlier_scope := (SELECT b.scope FROM core_memory.beads AS b
                           WHERE b.bead_id = NEW.earlier_bead);
    NEW.later_scope := (SELECT b.scope FROM core_memory.beads AS b
                         WHERE b.bead_id = NEW.later_bead);
    RETURN NEW;
END
$$;

CREATE FUNCTION core_memory.name_links_carry_scopes() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    NEW.a_scope := (SELECT b.scope FROM core_memory.beads AS b WHERE b.bead_id = NEW.a_bead);
    NEW.b_scope := (SELECT b.scope FROM core_memory.beads AS b WHERE b.bead_id = NEW.b_bead);
    RETURN NEW;
END
$$;

-- The shape of the graph. An edge joins two beads of its own workspace and
-- points at source content. Only a label bead supersedes, and it follows
-- one bead or two. Only a group bead contains. The other relationships
-- join source content to source content.
CREATE FUNCTION core_memory.edges_keep_their_shape() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    from_kind text;
    to_kind text;
BEGIN
    SELECT b.kind INTO from_kind FROM core_memory.beads AS b
     WHERE b.bead_id = NEW.from_bead AND b.workspace_id = NEW.workspace_id;
    SELECT b.kind INTO to_kind FROM core_memory.beads AS b
     WHERE b.bead_id = NEW.to_bead AND b.workspace_id = NEW.workspace_id;
    IF from_kind IS NULL OR to_kind IS NULL THEN
        RAISE EXCEPTION 'an edge joins two beads of its own workspace'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.edge_type = 'supersedes' THEN
        IF from_kind <> 'label' OR to_kind NOT IN ('chunk', 'tool') THEN
            RAISE EXCEPTION 'a supersedes edge runs from a label bead to the bead it follows'
                USING ERRCODE = 'check_violation';
        END IF;
        -- A chain grows at its head. The later statement is current when
        -- its label follows another bead, so it never follows its own
        -- source, and no circle can close: every bead of a circle would be
        -- superseded.
        IF NOT EXISTS (
            SELECT 1 FROM core_memory.labels AS l
             WHERE l.bead_id = NEW.from_bead AND l.source_bead <> NEW.to_bead
               AND NOT EXISTS (
                   SELECT 1 FROM core_memory.edges AS x
                    WHERE x.to_bead = l.source_bead AND x.edge_type = 'supersedes'))
        THEN
            RAISE EXCEPTION 'a supersedes edge runs from the label of a current statement to another bead'
                USING ERRCODE = 'check_violation';
        END IF;
        IF (SELECT count(*) FROM core_memory.edges AS e
             WHERE e.from_bead = NEW.from_bead AND e.edge_type = 'supersedes') >= 2 THEN
            RAISE EXCEPTION 'a label bead follows at most two beads'
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF NEW.edge_type = 'contains' THEN
        IF from_kind <> 'group' OR to_kind NOT IN ('chunk', 'tool') THEN
            RAISE EXCEPTION 'a contains edge runs from a group bead to a member'
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF from_kind NOT IN ('chunk', 'tool') OR to_kind NOT IN ('chunk', 'tool') THEN
        RAISE EXCEPTION 'a % edge joins two beads of source content', NEW.edge_type
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

-- A scope keeps its name and its workspace. Only its state changes.
CREATE FUNCTION core_memory.scopes_keep_identity() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF TG_OP = 'UPDATE'
        AND NEW.scope = OLD.scope
        AND NEW.workspace_id = OLD.workspace_id
        AND NEW.created_at = OLD.created_at
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'a scope cannot be renamed, moved or deleted'
        USING ERRCODE = 'restrict_violation';
END
$$;

-- A counter that went back would let a reader skip rows.
CREATE FUNCTION core_memory.seq_only_grows() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.workspace_id = OLD.workspace_id
        AND NEW.last_seq > OLD.last_seq THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'a workspace change counter only moves forward'
        USING ERRCODE = 'restrict_violation';
END
$$;

CREATE TRIGGER workspaces_fixed BEFORE UPDATE OR DELETE ON core_memory.workspaces
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER workspace_seq_grows BEFORE UPDATE OR DELETE ON core_memory.workspace_seq
    FOR EACH ROW EXECUTE FUNCTION core_memory.seq_only_grows();
CREATE TRIGGER scopes_keep_identity BEFORE UPDATE OR DELETE ON core_memory.scopes
    FOR EACH ROW EXECUTE FUNCTION core_memory.scopes_keep_identity();
CREATE TRIGGER events_no_delete BEFORE DELETE ON core_memory.events
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER events_only_erase BEFORE UPDATE ON core_memory.events
    FOR EACH ROW EXECUTE FUNCTION core_memory.events_only_erase();
CREATE TRIGGER chunks_fixed BEFORE UPDATE OR DELETE ON core_memory.chunks
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER chunk_text_fixed BEFORE UPDATE ON core_memory.chunk_text
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER chunk_text_only_erase BEFORE DELETE ON core_memory.chunk_text
    FOR EACH ROW EXECUTE FUNCTION core_memory.deleted_only_with_its_event();
CREATE TRIGGER beads_no_delete BEFORE DELETE ON core_memory.beads
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER beads_only_erase BEFORE UPDATE ON core_memory.beads
    FOR EACH ROW EXECUTE FUNCTION core_memory.beads_only_erase();
CREATE TRIGGER pins_fixed BEFORE UPDATE OR DELETE ON core_memory.pins
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER erasures_fixed BEFORE UPDATE OR DELETE ON core_memory.erasures
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER outbound_calls_fixed BEFORE UPDATE OR DELETE ON core_memory.outbound_calls
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER outbound_results_fixed BEFORE UPDATE OR DELETE ON core_memory.outbound_results
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER chunk_reads_fixed BEFORE UPDATE ON core_memory.chunk_reads
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER chunk_reads_only_erase BEFORE DELETE ON core_memory.chunk_reads
    FOR EACH ROW EXECUTE FUNCTION core_memory.deleted_only_with_its_event();
-- A span belongs to a read, so it needs no such check of its own.
CREATE TRIGGER chunk_reads_live BEFORE INSERT ON core_memory.chunk_reads
    FOR EACH ROW EXECUTE FUNCTION core_memory.stored_only_with_a_live_event();
CREATE TRIGGER chunk_read_spans_fixed BEFORE UPDATE ON core_memory.chunk_read_spans
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER chunk_read_spans_only_erase BEFORE DELETE ON core_memory.chunk_read_spans
    FOR EACH ROW EXECUTE FUNCTION core_memory.deleted_only_with_its_event();
CREATE TRIGGER tool_outcomes_fixed BEFORE UPDATE ON core_memory.tool_outcomes
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER tool_outcomes_only_erase BEFORE DELETE ON core_memory.tool_outcomes
    FOR EACH ROW EXECUTE FUNCTION core_memory.deleted_only_when_its_event_is_erased();
CREATE TRIGGER tool_outcomes_live BEFORE INSERT ON core_memory.tool_outcomes
    FOR EACH ROW EXECUTE FUNCTION core_memory.stored_only_when_its_event_is_live();
CREATE TRIGGER judge_reads_fixed BEFORE UPDATE OR DELETE ON core_memory.judge_reads
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER verdicts_fixed BEFORE UPDATE ON core_memory.verdicts
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER verdicts_only_erase BEFORE DELETE ON core_memory.verdicts
    FOR EACH ROW EXECUTE FUNCTION
        core_memory.deleted_only_when_a_bead_is_erased('earlier_bead', 'later_bead');
CREATE TRIGGER verdicts_live BEFORE INSERT ON core_memory.verdicts
    FOR EACH ROW EXECUTE FUNCTION
        core_memory.stored_only_for_live_beads('earlier_bead', 'later_bead');
CREATE TRIGGER verdicts_scopes BEFORE INSERT ON core_memory.verdicts
    FOR EACH ROW EXECUTE FUNCTION core_memory.verdicts_carry_scopes();
CREATE TRIGGER edges_fixed BEFORE UPDATE OR DELETE ON core_memory.edges
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER edges_shape BEFORE INSERT ON core_memory.edges
    FOR EACH ROW EXECUTE FUNCTION core_memory.edges_keep_their_shape();
CREATE TRIGGER edges_live BEFORE INSERT ON core_memory.edges
    FOR EACH ROW EXECUTE FUNCTION
        core_memory.stored_only_for_live_beads('from_bead', 'to_bead');
CREATE TRIGGER labels_fixed BEFORE UPDATE OR DELETE ON core_memory.labels
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER labels_live BEFORE INSERT ON core_memory.labels
    FOR EACH ROW EXECUTE FUNCTION
        core_memory.stored_only_for_live_beads('bead_id', 'source_bead');
CREATE TRIGGER pending_labels_fixed BEFORE UPDATE ON core_memory.pending_labels
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER pending_labels_live BEFORE INSERT ON core_memory.pending_labels
    FOR EACH ROW EXECUTE FUNCTION
        core_memory.stored_only_for_live_beads('source_bead', 'head_beads');
CREATE TRIGGER name_links_fixed BEFORE UPDATE ON core_memory.name_links
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER name_links_only_erase BEFORE DELETE ON core_memory.name_links
    FOR EACH ROW EXECUTE FUNCTION
        core_memory.deleted_only_when_a_bead_is_erased('a_bead', 'b_bead');
CREATE TRIGGER name_links_live BEFORE INSERT ON core_memory.name_links
    FOR EACH ROW EXECUTE FUNCTION core_memory.stored_only_for_live_beads('a_bead', 'b_bead');
CREATE TRIGGER name_links_scopes BEFORE INSERT ON core_memory.name_links
    FOR EACH ROW EXECUTE FUNCTION core_memory.name_links_carry_scopes();
CREATE TRIGGER name_links_ranges BEFORE INSERT ON core_memory.name_links
    FOR EACH ROW EXECUTE FUNCTION core_memory.name_links_within_their_chunks();

CREATE TRIGGER workspaces_no_truncate BEFORE TRUNCATE ON core_memory.workspaces
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER workspace_seq_no_truncate BEFORE TRUNCATE ON core_memory.workspace_seq
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER scopes_no_truncate BEFORE TRUNCATE ON core_memory.scopes
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON core_memory.events
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER chunks_no_truncate BEFORE TRUNCATE ON core_memory.chunks
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER chunk_text_no_truncate BEFORE TRUNCATE ON core_memory.chunk_text
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER beads_no_truncate BEFORE TRUNCATE ON core_memory.beads
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER checkpoints_no_truncate BEFORE TRUNCATE ON core_memory.checkpoints
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER pins_no_truncate BEFORE TRUNCATE ON core_memory.pins
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER erasures_no_truncate BEFORE TRUNCATE ON core_memory.erasures
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER scope_destinations_no_truncate BEFORE TRUNCATE ON core_memory.scope_destinations
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER outbound_calls_no_truncate BEFORE TRUNCATE ON core_memory.outbound_calls
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER outbound_results_no_truncate BEFORE TRUNCATE ON core_memory.outbound_results
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER chunk_reads_no_truncate BEFORE TRUNCATE ON core_memory.chunk_reads
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER chunk_read_spans_no_truncate BEFORE TRUNCATE ON core_memory.chunk_read_spans
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER tool_outcomes_no_truncate BEFORE TRUNCATE ON core_memory.tool_outcomes
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER judge_reads_no_truncate BEFORE TRUNCATE ON core_memory.judge_reads
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER verdicts_no_truncate BEFORE TRUNCATE ON core_memory.verdicts
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER edges_no_truncate BEFORE TRUNCATE ON core_memory.edges
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER labels_no_truncate BEFORE TRUNCATE ON core_memory.labels
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER pending_labels_no_truncate BEFORE TRUNCATE ON core_memory.pending_labels
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER name_links_no_truncate BEFORE TRUNCATE ON core_memory.name_links
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER work_no_truncate BEFORE TRUNCATE ON core_memory.work
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();

-- ---------------------------------------------------------------------------
-- Row security and the engine's privileges
-- ---------------------------------------------------------------------------

-- The engine reads and inserts. It holds no UPDATE or DELETE on events,
-- chunks, their text or beads, so it cannot alter or erase what it stored.
-- It holds nothing on workspace_seq: begin_write is the only way to it.
GRANT SELECT ON core_memory.workspaces, core_memory.scopes TO core_memory_engine;
GRANT SELECT, INSERT ON core_memory.events, core_memory.chunks, core_memory.chunk_text,
    core_memory.beads, core_memory.pins TO core_memory_engine;
GRANT SELECT, INSERT, UPDATE ON core_memory.checkpoints TO core_memory_engine;
GRANT SELECT ON core_memory.erasures TO core_memory_engine;
-- The engine reads consent and cannot give it. It writes receipts, results
-- and reads once, and owns the queue of its own work.
GRANT SELECT ON core_memory.scope_destinations TO core_memory_engine;
GRANT SELECT, INSERT ON core_memory.outbound_calls, core_memory.outbound_results,
    core_memory.chunk_reads, core_memory.chunk_read_spans, core_memory.tool_outcomes
    TO core_memory_engine;
GRANT SELECT, INSERT, UPDATE, DELETE ON core_memory.work TO core_memory_engine;
-- The engine writes what a judge said once. A label that waits for its
-- second read is its own to drop.
GRANT SELECT, INSERT ON core_memory.judge_reads, core_memory.verdicts, core_memory.edges,
    core_memory.labels, core_memory.name_links TO core_memory_engine;
GRANT SELECT, INSERT, DELETE ON core_memory.pending_labels TO core_memory_engine;

-- The history of migrations holds versions and checksums, no data. Every
-- role that is granted to read it reads all of it, to learn the store's
-- version: a host's own roles too, which would otherwise read no version
-- and be told to migrate. Who may read it is decided by the grant. Only
-- the owner, who migrates, writes it.
ALTER TABLE core_memory.schema_migrations ENABLE ROW LEVEL SECURITY;
CREATE POLICY reads ON core_memory.schema_migrations FOR SELECT TO PUBLIC USING (true);
ALTER TABLE core_memory.workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.workspace_seq ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.scopes ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.events ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.chunk_text ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.beads ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.pins ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.erasures ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.scope_destinations ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.outbound_calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.outbound_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.chunk_reads ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.chunk_read_spans ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.tool_outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.judge_reads ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.verdicts ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.labels ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.pending_labels ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.name_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_memory.work ENABLE ROW LEVEL SECURITY;

CREATE POLICY engine ON core_memory.workspaces TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.scopes TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.events TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.chunks TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.chunk_text TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.beads TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.checkpoints TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.pins TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.erasures TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.scope_destinations TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.outbound_calls TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.outbound_results TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.chunk_reads TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.chunk_read_spans TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.tool_outcomes TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.judge_reads TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.verdicts TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.edges TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.labels TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.pending_labels TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.name_links TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.work TO core_memory_engine USING (true) WITH CHECK (true);

-- ---------------------------------------------------------------------------
-- The two locks every writer takes, in this order
--
-- Every function below that writes runs as the schema owner with a pinned
-- search_path. pg_temp is named last on purpose: left out, it would be
-- searched first for types and tables, and a caller's temporary objects
-- would shadow the built-in ones.
-- ---------------------------------------------------------------------------

-- Holds the schema as it is until the calling transaction ends.
--
-- This is the first thing begin_write and every governance function do,
-- before they read any table. A migration takes schema_migrations
-- exclusively before it changes anything, so a caller that arrives during
-- one waits here holding no other lock, and the two cannot deadlock. For
-- the same reason the engine's own reads name schema_migrations first.
CREATE FUNCTION core_memory.hold_schema() RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    -- At REPEATABLE READ or SERIALIZABLE, what this transaction reads
    -- after waiting for a lock would be as old as its snapshot, and miss
    -- what the writers it waited for committed. PostgreSQL runs READ
    -- UNCOMMITTED as READ COMMITTED.
    IF current_setting('transaction_isolation') NOT IN ('read committed', 'read uncommitted') THEN
        RAISE EXCEPTION 'core_memory writes need READ COMMITTED isolation, not %',
            upper(current_setting('transaction_isolation'))
            USING ERRCODE = 'feature_not_supported';
    END IF;
    LOCK TABLE core_memory.schema_migrations IN ACCESS SHARE MODE;
END
$$;

-- Makes the calling transaction the workspace's writer until it ends. The
-- caller holds the schema.
--
-- Every writer calls this before it reads anything it will act on: the
-- engine's begin_write and every governance function. One lock per
-- workspace, taken first, means writers of a workspace never deadlock with
-- each other, in any order a host combines them in one transaction.
CREATE FUNCTION core_memory.lock_workspace(target_workspace text) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    PERFORM 1 FROM core_memory.workspace_seq AS q
      WHERE q.workspace_id = target_workspace FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'workspace % does not exist', quote_literal(target_workspace)
            USING ERRCODE = 'foreign_key_violation';
    END IF;
END
$$;

-- Takes how_many consecutive numbers from the workspace's change counter
-- and returns the first. The caller holds the workspace.
CREATE FUNCTION core_memory.take_seq(target_workspace text, how_many integer) RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    last bigint;
BEGIN
    IF how_many IS NULL OR how_many < 1 THEN
        RAISE EXCEPTION 'take_seq needs a count of 1 or more'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE core_memory.workspace_seq AS q
       SET last_seq = q.last_seq + how_many
     WHERE q.workspace_id = target_workspace
    RETURNING q.last_seq INTO last;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'workspace % does not exist', quote_literal(target_workspace)
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN last - how_many + 1;
END
$$;

-- ---------------------------------------------------------------------------
-- The function the engine calls
-- ---------------------------------------------------------------------------

-- Starts a transaction that writes for a scope. It holds the schema,
-- refuses an engine built for another schema version, makes the transaction
-- the workspace's writer, and returns the scope's workspace and state as
-- they are under that lock. With take, an active scope's workspace also
-- gives up that many numbers from its change counter, and first_seq is the
-- first of them.
--
-- No row means the scope is not enrolled. Pausing, revoking and erasing
-- take the same lock, so none of them interleaves with a write.
CREATE FUNCTION core_memory.begin_write(
    target_scope text, engine_schema integer, take integer
)
RETURNS TABLE (workspace_id text, state text, first_seq bigint)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    store_schema integer;
    scope_workspace text;
    scope_state text;
BEGIN
    PERFORM core_memory.hold_schema();
    SELECT max(m.version) INTO store_schema FROM core_memory.schema_migrations AS m;
    IF store_schema IS DISTINCT FROM engine_schema THEN
        RAISE EXCEPTION 'the store is at schema version %; this engine was built for version %',
            store_schema, engine_schema
            USING ERRCODE = 'CM001';
    END IF;
    -- A scope never changes workspace, so this needs no lock.
    SELECT s.workspace_id INTO scope_workspace
      FROM core_memory.scopes AS s WHERE s.scope = target_scope;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    PERFORM core_memory.lock_workspace(scope_workspace);
    SELECT s.state INTO scope_state
      FROM core_memory.scopes AS s WHERE s.scope = target_scope;
    workspace_id := scope_workspace;
    state := scope_state;
    IF scope_state = 'active' AND take IS NOT NULL THEN
        first_seq := core_memory.take_seq(scope_workspace, take);
    END IF;
    RETURN NEXT;
END
$$;

-- Starts a transaction that writes for a workspace as a whole, as a judge's
-- read does: it is shown beads of every scope of the workspace. It holds
-- the schema, refuses an engine built for another schema version, and
-- makes the transaction the workspace's writer. With take, the workspace
-- gives up that many numbers from its change counter, and the first is
-- returned.
--
-- It says nothing about any scope. What the transaction may read and write
-- for each scope, it decides from that scope's state under this lock.
CREATE FUNCTION core_memory.begin_workspace_write(
    target_workspace text, engine_schema integer, take integer
)
RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    store_schema integer;
BEGIN
    PERFORM core_memory.hold_schema();
    SELECT max(m.version) INTO store_schema FROM core_memory.schema_migrations AS m;
    IF store_schema IS DISTINCT FROM engine_schema THEN
        RAISE EXCEPTION 'the store is at schema version %; this engine was built for version %',
            store_schema, engine_schema
            USING ERRCODE = 'CM001';
    END IF;
    PERFORM core_memory.lock_workspace(target_workspace);
    IF take IS NULL THEN
        RETURN NULL;
    END IF;
    RETURN core_memory.take_seq(target_workspace, take);
END
$$;

-- ---------------------------------------------------------------------------
-- Governance: functions a host or an operator calls
--
-- Each holds the schema before anything else, then the workspace it changes.
-- A host that calls one inside a transaction of its own should call it
-- before that transaction reads any core_memory table: a transaction that
-- already holds a table and then waits here can deadlock with a migration.
--
-- A host transaction that changes more than one workspace should take them
-- in workspace ID order, which is the order erase_events takes its own.
-- Two transactions that take the same two workspaces in opposite orders
-- can deadlock. PostgreSQL ends one of them; it changed nothing and can be
-- run again.
-- ---------------------------------------------------------------------------

-- Creates the workspace with a fresh ID key. Returns false if it exists.
CREATE FUNCTION core_memory.create_workspace(new_workspace_id text) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    inserted integer;
BEGIN
    PERFORM core_memory.hold_schema();
    INSERT INTO core_memory.workspaces AS w (workspace_id, id_key, created_at)
    VALUES (
        new_workspace_id,
        decode(replace(gen_random_uuid()::text, '-', '')
            || replace(gen_random_uuid()::text, '-', ''), 'hex'),
        clock_timestamp()
    )
    ON CONFLICT ON CONSTRAINT workspaces_pkey DO NOTHING;
    GET DIAGNOSTICS inserted = ROW_COUNT;
    IF inserted = 1 THEN
        INSERT INTO core_memory.workspace_seq AS q (workspace_id, last_seq)
        VALUES (new_workspace_id, 0);
    END IF;
    RETURN inserted = 1;
END
$$;

-- Enrolls a scope in a workspace, or makes a revoked scope active again.
-- Returns 'enrolled', 'reactivated' or 'unchanged'. A scope never moves to
-- another workspace, because its IDs are keyed by the first.
CREATE FUNCTION core_memory.enroll_scope(new_scope text, in_workspace text) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    inserted integer;
    existing_workspace text;
    existing_state text;
BEGIN
    PERFORM core_memory.hold_schema();
    PERFORM core_memory.lock_workspace(in_workspace);
    INSERT INTO core_memory.scopes AS s (scope, workspace_id, state, created_at, state_changed_at)
    VALUES (new_scope, in_workspace, 'active', clock_timestamp(), clock_timestamp())
    ON CONFLICT ON CONSTRAINT scopes_pkey DO NOTHING;
    GET DIAGNOSTICS inserted = ROW_COUNT;
    IF inserted = 1 THEN
        RETURN 'enrolled';
    END IF;
    SELECT s.workspace_id, s.state INTO existing_workspace, existing_state
      FROM core_memory.scopes AS s WHERE s.scope = new_scope;
    IF existing_workspace <> in_workspace THEN
        RAISE EXCEPTION 'scope % belongs to another workspace', quote_literal(new_scope)
            USING ERRCODE = 'check_violation';
    END IF;
    IF existing_state = 'revoked' THEN
        UPDATE core_memory.scopes AS s
           SET state = 'active', state_changed_at = clock_timestamp()
         WHERE s.scope = new_scope;
        -- Work the engine put off while the scope was revoked is due again.
        UPDATE core_memory.work AS w
           SET not_before = w.enqueued_at, problem = NULL
         WHERE w.scope = new_scope AND w.state = 'ready' AND w.problem = 'scope_not_active';
        RETURN 'reactivated';
    END IF;
    RETURN 'unchanged';
END
$$;

-- Pauses, resumes or revokes a scope. Returns the state it had. Revoking
-- is final for this function; enroll_scope reconnects a revoked scope.
CREATE FUNCTION core_memory.set_scope_state(target text, new_state text) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    scope_workspace text;
    old_state text;
BEGIN
    PERFORM core_memory.hold_schema();
    IF new_state IS NULL OR new_state NOT IN ('active', 'paused', 'revoked') THEN
        RAISE EXCEPTION 'a scope is active, paused or revoked'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT s.workspace_id INTO scope_workspace
      FROM core_memory.scopes AS s WHERE s.scope = target;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'scope % is not enrolled', quote_literal(target)
            USING ERRCODE = 'no_data_found';
    END IF;
    PERFORM core_memory.lock_workspace(scope_workspace);
    SELECT s.state INTO old_state FROM core_memory.scopes AS s WHERE s.scope = target;
    IF old_state = new_state THEN
        RETURN old_state;
    END IF;
    IF old_state = 'revoked' THEN
        RAISE EXCEPTION 'scope % is revoked; enroll it again to reconnect it', quote_literal(target)
            USING ERRCODE = 'check_violation';
    END IF;
    UPDATE core_memory.scopes AS s
       SET state = new_state, state_changed_at = clock_timestamp()
     WHERE s.scope = target;
    IF new_state = 'active' THEN
        -- Work the engine put off while the scope was paused is due again.
        UPDATE core_memory.work AS w
           SET not_before = w.enqueued_at, problem = NULL
         WHERE w.scope = target AND w.state = 'ready' AND w.problem = 'scope_not_active';
    END IF;
    RETURN old_state;
END
$$;

-- Lets a scope's text be sent to a model destination: the host, and port if
-- any, of the model's endpoint. Returns 'granted' or 'unchanged'.
CREATE FUNCTION core_memory.grant_destination(target_scope text, target_destination text)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    scope_workspace text;
    was text;
BEGIN
    PERFORM core_memory.hold_schema();
    IF target_destination IS NULL OR btrim(target_destination) = ''
        OR target_destination <> lower(btrim(target_destination)) THEN
        RAISE EXCEPTION 'a destination is a lower-case host name, with a port if the endpoint has one'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT s.workspace_id INTO scope_workspace
      FROM core_memory.scopes AS s WHERE s.scope = target_scope;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'scope % is not enrolled', quote_literal(target_scope)
            USING ERRCODE = 'no_data_found';
    END IF;
    PERFORM core_memory.lock_workspace(scope_workspace);
    SELECT d.state INTO was FROM core_memory.scope_destinations AS d
     WHERE d.scope = target_scope AND d.destination = target_destination;
    IF was = 'granted' THEN
        RETURN 'unchanged';
    END IF;
    INSERT INTO core_memory.scope_destinations AS d
        (scope, destination, state, granted_at, revoked_at)
    VALUES (target_scope, target_destination, 'granted', clock_timestamp(), NULL)
    ON CONFLICT ON CONSTRAINT scope_destinations_pkey DO UPDATE
        SET state = 'granted', granted_at = EXCLUDED.granted_at, revoked_at = NULL;
    -- Work the engine put off for want of consent is due again, as from
    -- when it was queued. 'no_consent' is the engine's code for it.
    UPDATE core_memory.work AS w
       SET not_before = w.enqueued_at, problem = NULL
     WHERE w.scope = target_scope AND w.state = 'ready' AND w.problem = 'no_consent';
    RETURN 'granted';
END
$$;

-- Stops a scope's text from being sent to a destination. It takes effect
-- before the next call: a call's receipt is written under the same lock.
-- Returns 'revoked' or 'unchanged'.
CREATE FUNCTION core_memory.revoke_destination(target_scope text, target_destination text)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    scope_workspace text;
    changed integer;
BEGIN
    PERFORM core_memory.hold_schema();
    SELECT s.workspace_id INTO scope_workspace
      FROM core_memory.scopes AS s WHERE s.scope = target_scope;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'scope % is not enrolled', quote_literal(target_scope)
            USING ERRCODE = 'no_data_found';
    END IF;
    PERFORM core_memory.lock_workspace(scope_workspace);
    UPDATE core_memory.scope_destinations AS d
       SET state = 'revoked', revoked_at = clock_timestamp()
     WHERE d.scope = target_scope AND d.destination = target_destination
       AND d.state = 'granted';
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN CASE WHEN changed = 1 THEN 'revoked' ELSE 'unchanged' END;
END
$$;

-- Erases the given events of one workspace, which must all be live. Each
-- loses its text and facets, its key fields, hashes and metadata, and what
-- any model read in it; its beads are marked erased; its queued work is
-- dropped; one journal row is written for it. IDs, ranges, links and the
-- receipts of calls that sent it stay. The caller holds the workspace.
CREATE FUNCTION core_memory.erase_live_events(
    event_ids uuid[], in_workspace text, reason text, actor text
) RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    how_many integer := coalesce(cardinality(event_ids), 0);
    first_seq bigint;
BEGIN
    IF reason IS NULL OR btrim(reason) = '' OR actor IS NULL OR btrim(actor) = '' THEN
        RAISE EXCEPTION 'an erasure needs a reason and an actor'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF how_many = 0 THEN
        RETURN 0;
    END IF;
    first_seq := core_memory.take_seq(in_workspace, how_many);

    -- The journal first: it counts the text that is about to go.
    INSERT INTO core_memory.erasures AS j (
        workspace_id, seq, event_id, scope, reason, actor, chunks_erased, erased_at
    )
    SELECT e.workspace_id, first_seq + t.n - 1, e.event_id, e.scope, reason, actor,
           (SELECT count(*)
              FROM core_memory.chunks AS c
              JOIN core_memory.chunk_text AS x ON x.chunk_id = c.chunk_id
             WHERE c.event_id = e.event_id),
           clock_timestamp()
      FROM unnest(event_ids) WITH ORDINALITY AS t (event_id, n)
      JOIN core_memory.events AS e ON e.event_id = t.event_id
     WHERE e.workspace_id = in_workspace AND e.state = 'live';
    IF NOT FOUND OR (SELECT count(*) FROM core_memory.erasures AS j
                      WHERE j.workspace_id = in_workspace AND j.seq >= first_seq) <> how_many THEN
        RAISE EXCEPTION 'erase_live_events was given an event that is not live in workspace %',
            quote_literal(in_workspace)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    PERFORM set_config('core_memory.erasing', 'on', true);
    UPDATE core_memory.events AS e
       SET state = 'erased',
           source_ns = NULL, external_id = NULL, revision = NULL,
           engine_assigned_revision = NULL,
           event_hash = NULL, content_hash = NULL, info = NULL,
           time_at = e.captured_at, time_basis = 'captured',
           erased_at = clock_timestamp()
     WHERE e.event_id = ANY (event_ids);
    UPDATE core_memory.beads AS b
       SET state = 'erased', time_at = b.created_at, time_basis = 'captured'
     WHERE b.event_id = ANY (event_ids);
    DELETE FROM core_memory.chunk_text AS x
     USING core_memory.chunks AS c
     WHERE c.event_id = ANY (event_ids) AND x.chunk_id = c.chunk_id;
    -- Everything built from the text goes with it, and so does the work
    -- that would have sent it to a model.
    DELETE FROM core_memory.chunk_read_spans AS x
     USING core_memory.chunks AS c
     WHERE c.event_id = ANY (event_ids) AND x.chunk_id = c.chunk_id;
    DELETE FROM core_memory.chunk_reads AS x
     USING core_memory.chunks AS c
     WHERE c.event_id = ANY (event_ids) AND x.chunk_id = c.chunk_id;
    DELETE FROM core_memory.tool_outcomes AS x WHERE x.event_id = ANY (event_ids);
    DELETE FROM core_memory.work AS x WHERE x.event_id = ANY (event_ids);
    -- What a judge said about the erased beads. A verdict's columns and a
    -- name link's ranges were computed from their content, and a label
    -- that waits for its second read would have them read again.
    DELETE FROM core_memory.verdicts AS x
     USING core_memory.beads AS b
     WHERE b.event_id = ANY (event_ids) AND x.earlier_bead = b.bead_id;
    DELETE FROM core_memory.verdicts AS x
     USING core_memory.beads AS b
     WHERE b.event_id = ANY (event_ids) AND x.later_bead = b.bead_id;
    DELETE FROM core_memory.name_links AS x
     USING core_memory.beads AS b
     WHERE b.event_id = ANY (event_ids) AND x.a_bead = b.bead_id;
    DELETE FROM core_memory.name_links AS x
     USING core_memory.beads AS b
     WHERE b.event_id = ANY (event_ids) AND x.b_bead = b.bead_id;
    WITH gone AS (
        DELETE FROM core_memory.pending_labels AS x
         USING core_memory.beads AS b
         WHERE b.event_id = ANY (event_ids)
           AND (x.source_bead = b.bead_id OR b.bead_id = ANY (x.head_beads))
        RETURNING x.source_bead
    )
    DELETE FROM core_memory.work AS w
     USING gone
     WHERE w.kind = 'second_read' AND w.item_id = gone.source_bead;
    -- Edges stay: they hold IDs. A label bead goes with its source and
    -- keeps its place in its chain, so what it superseded does not become
    -- current again. A group bead goes with its last live member.
    UPDATE core_memory.beads AS l
       SET state = 'erased', time_at = l.created_at, time_basis = 'captured'
      FROM core_memory.labels AS x
      JOIN core_memory.beads AS source ON source.bead_id = x.source_bead
     WHERE l.bead_id = x.bead_id AND l.state = 'live' AND source.event_id = ANY (event_ids);
    UPDATE core_memory.beads AS g
       SET state = 'erased', time_at = g.created_at, time_basis = 'captured'
     WHERE g.kind = 'group' AND g.state = 'live' AND g.workspace_id = in_workspace
       AND EXISTS (
           SELECT 1 FROM core_memory.edges AS e
             JOIN core_memory.beads AS member ON member.bead_id = e.to_bead
            WHERE e.from_bead = g.bead_id AND e.edge_type = 'contains'
              AND member.event_id = ANY (event_ids))
       AND NOT EXISTS (
           SELECT 1 FROM core_memory.edges AS e
             JOIN core_memory.beads AS member ON member.bead_id = e.to_bead
            WHERE e.from_bead = g.bead_id AND e.edge_type = 'contains'
              AND member.state = 'live');
    PERFORM set_config('core_memory.erasing', 'off', true);
    RETURN how_many;
END
$$;

-- Erases one whole message. Returns 'erased', 'already_erased' or
-- 'unknown'. An erased event is refused if it is offered again.
CREATE FUNCTION core_memory.erase_event(target uuid, reason text, actor text) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    event_workspace text;
    event_state text;
BEGIN
    PERFORM core_memory.hold_schema();
    IF reason IS NULL OR btrim(reason) = '' OR actor IS NULL OR btrim(actor) = '' THEN
        RAISE EXCEPTION 'an erasure needs a reason and an actor'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT e.workspace_id INTO event_workspace
      FROM core_memory.events AS e WHERE e.event_id = target;
    IF NOT FOUND THEN
        RETURN 'unknown';
    END IF;
    PERFORM core_memory.lock_workspace(event_workspace);
    SELECT e.state INTO event_state FROM core_memory.events AS e WHERE e.event_id = target;
    IF event_state = 'erased' THEN
        RETURN 'already_erased';
    END IF;
    PERFORM core_memory.erase_live_events(ARRAY[target], event_workspace, reason, actor);
    RETURN 'erased';
END
$$;

-- Erases many whole messages in one call. Returns one row for each
-- distinct ID given: 'erased', 'already_erased' or 'unknown'.
--
-- One call takes each workspace once, however many of its events it
-- erases, so this is the way to erase more than a handful: thousands of
-- erase_event calls in one transaction hold the workspace for as long as
-- they take, and each is slower than the one before.
CREATE FUNCTION core_memory.erase_events(targets uuid[], reason text, actor text)
RETURNS TABLE (event_id uuid, outcome text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    wanted uuid[];
    held text[];
    workspace text;
    live uuid[];
BEGIN
    PERFORM core_memory.hold_schema();
    IF reason IS NULL OR btrim(reason) = '' OR actor IS NULL OR btrim(actor) = '' THEN
        RAISE EXCEPTION 'an erasure needs a reason and an actor'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF targets IS NULL OR array_ndims(targets) > 1 OR array_position(targets, NULL) IS NOT NULL THEN
        RAISE EXCEPTION 'erase_events needs a list of event IDs, none of them null'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT coalesce(array_agg(DISTINCT t.id), '{}') INTO wanted FROM unnest(targets) AS t (id);
    -- Every workspace is taken before anything is erased, and in workspace
    -- ID order, so two calls never wait for each other in a ring. The
    -- order is this call's own: a transaction that already holds a later
    -- workspace when it calls can still form one, as the header says.
    SELECT coalesce(array_agg(DISTINCT e.workspace_id ORDER BY e.workspace_id), '{}') INTO held
      FROM core_memory.events AS e WHERE e.event_id = ANY (wanted);
    FOREACH workspace IN ARRAY held LOOP
        PERFORM core_memory.lock_workspace(workspace);
    END LOOP;
    -- What each event is now that nothing else writes to those workspaces.
    -- An event admitted to another workspace since the lookup above was
    -- not there when this call looked, and is reported as unknown.
    RETURN QUERY
        SELECT t.id,
               CASE e.state WHEN 'live' THEN 'erased'
                            WHEN 'erased' THEN 'already_erased'
                            ELSE 'unknown' END
          FROM unnest(wanted) AS t (id)
          LEFT JOIN core_memory.events AS e
                 ON e.event_id = t.id AND e.workspace_id = ANY (held)
         ORDER BY t.id;
    FOREACH workspace IN ARRAY held LOOP
        SELECT coalesce(array_agg(e.event_id ORDER BY e.seq), '{}') INTO live
          FROM core_memory.events AS e
         WHERE e.event_id = ANY (wanted) AND e.workspace_id = workspace AND e.state = 'live';
        PERFORM core_memory.erase_live_events(live, workspace, reason, actor);
    END LOOP;
END
$$;

-- Erases every live revision of one source object: every event of the
-- scope with this source_ns and external_id. Returns how many were erased.
CREATE FUNCTION core_memory.erase_key(
    target_scope text, target_source_ns text, target_external_id text, reason text, actor text
) RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    scope_workspace text;
    live uuid[];
BEGIN
    PERFORM core_memory.hold_schema();
    SELECT s.workspace_id INTO scope_workspace
      FROM core_memory.scopes AS s WHERE s.scope = target_scope;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'scope % is not enrolled', quote_literal(target_scope)
            USING ERRCODE = 'no_data_found';
    END IF;
    PERFORM core_memory.lock_workspace(scope_workspace);
    SELECT coalesce(array_agg(e.event_id ORDER BY e.seq), '{}') INTO live
      FROM core_memory.events AS e
     WHERE e.scope = target_scope AND e.source_ns = target_source_ns
       AND e.external_id = target_external_id AND e.state = 'live';
    RETURN core_memory.erase_live_events(live, scope_workspace, reason, actor);
END
$$;

-- Erases live events of a scope, oldest first: all of them, or at most
-- max_events. Returns how many were erased; 0 means none are left.
--
-- It holds the workspace for as long as it runs, so no event is admitted
-- to the scope halfway through. A caller that erases a large scope in
-- batches should stop capture first, by pausing or revoking the scope.
CREATE FUNCTION core_memory.erase_scope(
    target text, reason text, actor text, max_events integer DEFAULT NULL
) RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    scope_workspace text;
    live uuid[];
BEGIN
    PERFORM core_memory.hold_schema();
    IF max_events IS NOT NULL AND max_events < 1 THEN
        RAISE EXCEPTION 'max_events must be 1 or more'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT s.workspace_id INTO scope_workspace
      FROM core_memory.scopes AS s WHERE s.scope = target;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'scope % is not enrolled', quote_literal(target)
            USING ERRCODE = 'no_data_found';
    END IF;
    PERFORM core_memory.lock_workspace(scope_workspace);
    SELECT coalesce(array_agg(batch.event_id ORDER BY batch.seq), '{}') INTO live
      FROM (
          SELECT e.event_id, e.seq
            FROM core_memory.events AS e
           WHERE e.scope = target AND e.state = 'live'
           ORDER BY e.seq
           LIMIT max_events
      ) AS batch;
    RETURN core_memory.erase_live_events(live, scope_workspace, reason, actor);
END
$$;

-- Functions are executable by PUBLIC unless that is taken away.
REVOKE ALL ON FUNCTION core_memory.refuse_change() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.events_only_erase() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.beads_only_erase() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.deleted_only_with_its_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.deleted_only_when_its_event_is_erased() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.stored_only_with_a_live_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.stored_only_when_its_event_is_live() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.deleted_only_when_a_bead_is_erased() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.stored_only_for_live_beads() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.name_links_within_their_chunks() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.verdicts_carry_scopes() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.name_links_carry_scopes() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.edges_keep_their_shape() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.scopes_keep_identity() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.seq_only_grows() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.hold_schema() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.lock_workspace(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.take_seq(text, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.begin_write(text, integer, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.begin_workspace_write(text, integer, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.create_workspace(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.enroll_scope(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.set_scope_state(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.grant_destination(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.revoke_destination(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.erase_live_events(uuid[], text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.erase_event(uuid, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.erase_events(uuid[], text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.erase_key(text, text, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.erase_scope(text, text, text, integer) FROM PUBLIC;

-- hold_schema, lock_workspace, take_seq and erase_live_events are called
-- only by the functions above, as the schema owner.
GRANT EXECUTE ON FUNCTION core_memory.begin_write(text, integer, integer) TO core_memory_engine;
GRANT EXECUTE ON FUNCTION core_memory.begin_workspace_write(text, integer, integer)
    TO core_memory_engine;

GRANT EXECUTE ON FUNCTION core_memory.create_workspace(text) TO core_memory_governor;
GRANT EXECUTE ON FUNCTION core_memory.enroll_scope(text, text) TO core_memory_governor;
GRANT EXECUTE ON FUNCTION core_memory.set_scope_state(text, text) TO core_memory_governor;
GRANT EXECUTE ON FUNCTION core_memory.grant_destination(text, text) TO core_memory_governor;
GRANT EXECUTE ON FUNCTION core_memory.revoke_destination(text, text) TO core_memory_governor;
GRANT EXECUTE ON FUNCTION core_memory.erase_event(uuid, text, text) TO core_memory_governor;
GRANT EXECUTE ON FUNCTION core_memory.erase_events(uuid[], text, text) TO core_memory_governor;
GRANT EXECUTE ON FUNCTION core_memory.erase_key(text, text, text, text, text) TO core_memory_governor;
GRANT EXECUTE ON FUNCTION core_memory.erase_scope(text, text, text, integer) TO core_memory_governor;
