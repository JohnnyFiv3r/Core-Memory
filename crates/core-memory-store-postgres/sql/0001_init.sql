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
    scope text NOT NULL REFERENCES core_memory.scopes,
    kind text NOT NULL CHECK (kind IN ('chunk')),
    state text NOT NULL CHECK (state IN ('live', 'erased')),
    event_id uuid NOT NULL REFERENCES core_memory.events,
    chunk_id uuid REFERENCES core_memory.chunks,
    created_by text NOT NULL CHECK (created_by IN ('engine')),
    time_at timestamptz NOT NULL,
    time_basis text NOT NULL CHECK (time_basis IN ('source', 'source_bucket', 'captured')),
    created_at timestamptz NOT NULL,
    seq bigint NOT NULL CHECK (seq > 0),
    -- A message chunk's bead has the chunk's own ID.
    CHECK ((kind = 'chunk') = (chunk_id IS NOT NULL AND bead_id = chunk_id)),
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
        AND NEW.scope = OLD.scope
        AND NEW.kind = OLD.kind
        AND NEW.event_id = OLD.event_id
        AND NEW.chunk_id IS NOT DISTINCT FROM OLD.chunk_id
        AND NEW.created_by = OLD.created_by
        AND NEW.created_at = OLD.created_at
        AND NEW.seq = OLD.seq
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'core_memory.beads rows cannot be changed'
        USING ERRCODE = 'restrict_violation';
END
$$;

-- Text is deleted only for an event that has just been erased.
CREATE FUNCTION core_memory.chunk_text_only_erase() RETURNS trigger
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
    RAISE EXCEPTION 'core_memory.chunk_text rows cannot be deleted'
        USING ERRCODE = 'restrict_violation';
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
    FOR EACH ROW EXECUTE FUNCTION core_memory.chunk_text_only_erase();
CREATE TRIGGER beads_no_delete BEFORE DELETE ON core_memory.beads
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER beads_only_erase BEFORE UPDATE ON core_memory.beads
    FOR EACH ROW EXECUTE FUNCTION core_memory.beads_only_erase();
CREATE TRIGGER pins_fixed BEFORE UPDATE OR DELETE ON core_memory.pins
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
CREATE TRIGGER erasures_fixed BEFORE UPDATE OR DELETE ON core_memory.erasures
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();

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

CREATE POLICY engine ON core_memory.workspaces TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.scopes TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.events TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.chunks TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.chunk_text TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.beads TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.checkpoints TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.pins TO core_memory_engine USING (true) WITH CHECK (true);
CREATE POLICY engine ON core_memory.erasures TO core_memory_engine USING (true) WITH CHECK (true);

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
    RETURN old_state;
END
$$;

-- Erases the given events of one workspace, which must all be live. Each
-- loses its text and facets, its key fields, hashes and metadata; its
-- beads are marked erased; one journal row is written for it. IDs, ranges
-- and links stay as the tombstone. The caller holds the workspace.
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
REVOKE ALL ON FUNCTION core_memory.chunk_text_only_erase() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.scopes_keep_identity() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.seq_only_grows() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.hold_schema() FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.lock_workspace(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.take_seq(text, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.begin_write(text, integer, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.create_workspace(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.enroll_scope(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.set_scope_state(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.erase_live_events(uuid[], text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.erase_event(uuid, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.erase_events(uuid[], text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.erase_key(text, text, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core_memory.erase_scope(text, text, text, integer) FROM PUBLIC;

-- hold_schema, lock_workspace, take_seq and erase_live_events are called
-- only by the functions above, as the schema owner.
GRANT EXECUTE ON FUNCTION core_memory.begin_write(text, integer, integer) TO core_memory_engine;

GRANT EXECUTE ON FUNCTION core_memory.create_workspace(text) TO core_memory_governor;
GRANT EXECUTE ON FUNCTION core_memory.enroll_scope(text, text) TO core_memory_governor;
GRANT EXECUTE ON FUNCTION core_memory.set_scope_state(text, text) TO core_memory_governor;
GRANT EXECUTE ON FUNCTION core_memory.erase_event(uuid, text, text) TO core_memory_governor;
GRANT EXECUTE ON FUNCTION core_memory.erase_events(uuid[], text, text) TO core_memory_governor;
GRANT EXECUTE ON FUNCTION core_memory.erase_key(text, text, text, text, text) TO core_memory_governor;
GRANT EXECUTE ON FUNCTION core_memory.erase_scope(text, text, text, integer) TO core_memory_governor;
