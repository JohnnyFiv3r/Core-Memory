-- Core Memory engine schema, version 4: a vector for each chunk.
--
-- Recall finds chunks by their words (migration 2). With an embedder
-- configured it also finds them by meaning: a model gives each chunk a
-- vector, a query gets one the same way, and the chunks whose vectors are
-- nearest the query's are found however they are worded.
--
-- A vector is an index of a chunk's text, as chunk_text_search_idx is. It
-- is not evidence and not a relationship: recall returns the stored text
-- of the chunks it finds, and no edge is ever written from a distance.
--
-- A vector is derived from text, and text can be guessed back from it. So
-- a row here exists only while its chunk's text does. The key to
-- chunk_text says so, and it is the one key in this schema that deletes:
-- erasing a message deletes its text, and that takes the vectors with it,
-- in the same statement. Nothing else deletes a row, and nothing changes
-- one.
--
-- Vectors are stored as real[], which every PostgreSQL has, so this
-- migration needs no extension and applies to every database. Comparing
-- them needs the `vector` extension (pgvector), and recall looks for it
-- when it is asked to: without it vectors are still stored, and recall
-- answers from the words and says so.
--
-- A chunk has one vector for each revision of the embedder: its provider,
-- endpoint, model, vector length and prefixes. Vectors of two revisions
-- are never compared. A tool call's chunks get none: a tool call's text is
-- never sent to a model whole.

CREATE TABLE core_memory.chunk_vectors (
    workspace_id text NOT NULL REFERENCES core_memory.workspaces,
    seq bigint NOT NULL CHECK (seq > 0),
    chunk_id uuid NOT NULL REFERENCES core_memory.chunk_text ON DELETE CASCADE,
    scope text NOT NULL REFERENCES core_memory.scopes,
    call_id uuid NOT NULL REFERENCES core_memory.outbound_calls,
    embedder_rev bytea NOT NULL CHECK (pg_catalog.octet_length(embedder_rev) = 32),
    binding_id text NOT NULL,
    model text NOT NULL,
    dimensions integer NOT NULL CHECK (dimensions BETWEEN 1 AND 16000),
    -- Bytes of the chunk's text the vector was made from, from its start.
    input_bytes bigint NOT NULL CHECK (input_bytes > 0),
    vector real[] NOT NULL,
    embedded_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, seq),
    UNIQUE (chunk_id, embedder_rev),
    CHECK (pg_catalog.array_ndims(vector) = 1
           AND pg_catalog.cardinality(vector) = dimensions
           AND pg_catalog.array_position(vector, NULL) IS NULL)
);

CREATE INDEX chunk_vectors_scope_idx ON core_memory.chunk_vectors (scope, embedder_rev);

-- A vector is numbers that do not compress, so it is kept as it is. And it
-- is made from what a source said, so the planner keeps no sample of it,
-- as it keeps none of the text.
ALTER TABLE core_memory.chunk_vectors
    ALTER COLUMN vector SET STORAGE EXTERNAL,
    ALTER COLUMN vector SET STATISTICS 0;

CREATE TRIGGER chunk_vectors_fixed BEFORE UPDATE ON core_memory.chunk_vectors
    FOR EACH ROW EXECUTE FUNCTION core_memory.refuse_change();
-- The key to chunk_text deletes a vector with its text. This refuses every
-- other delete, as it does for the text itself.
CREATE TRIGGER chunk_vectors_only_erase BEFORE DELETE ON core_memory.chunk_vectors
    FOR EACH ROW EXECUTE FUNCTION core_memory.deleted_only_with_its_event();
CREATE TRIGGER chunk_vectors_live BEFORE INSERT ON core_memory.chunk_vectors
    FOR EACH ROW EXECUTE FUNCTION core_memory.stored_only_with_a_live_event();
CREATE TRIGGER chunk_vectors_no_truncate BEFORE TRUNCATE ON core_memory.chunk_vectors
    FOR EACH STATEMENT EXECUTE FUNCTION core_memory.refuse_change();

-- The engine stores a vector once and reads it for recall.
GRANT SELECT, INSERT ON core_memory.chunk_vectors TO core_memory_engine;
ALTER TABLE core_memory.chunk_vectors ENABLE ROW LEVEL SECURITY;
CREATE POLICY engine ON core_memory.chunk_vectors TO core_memory_engine
    USING (true) WITH CHECK (true);

-- Embedding is a kind of queued work and a purpose of a model call. Like a
-- read, a call that embeds is for one scope and names chunks, not beads.
ALTER TABLE core_memory.work
    DROP CONSTRAINT work_kind_check,
    ADD CONSTRAINT work_kind_check
        CHECK (kind IN ('chunk_read', 'tool_read', 'judge', 'second_read', 'embed'));

ALTER TABLE core_memory.outbound_calls
    DROP CONSTRAINT outbound_calls_purpose_check,
    DROP CONSTRAINT outbound_calls_check1,
    DROP CONSTRAINT outbound_calls_check2,
    ADD CONSTRAINT outbound_calls_purpose_check
        CHECK (purpose IN ('chunk_read', 'tool_read', 'judge', 'second_read', 'embed')),
    ADD CONSTRAINT outbound_calls_scope_check
        CHECK ((purpose IN ('chunk_read', 'tool_read', 'embed')) = (scope IS NOT NULL)),
    ADD CONSTRAINT outbound_calls_beads_check
        CHECK ((purpose IN ('chunk_read', 'tool_read', 'embed')) = (bead_ids IS NULL));
