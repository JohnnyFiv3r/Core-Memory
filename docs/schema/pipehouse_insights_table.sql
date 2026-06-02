-- PipeHouse → Core Memory insights table contract
-- PipeHouse writes rows here; Core Memory reads and ingests them as data_insight beads.
-- See: docs/PRD/external-data-bead-ingest.md

CREATE TABLE core_memory_insights (
    -- PipeHouse-assigned primary key; maps to bead.links["external_source_id"]
    id                      TEXT        NOT NULL PRIMARY KEY,

    -- Originating table in PipeHouse (e.g. "quickbooks_transactions", "pipeline_metrics")
    source_table            TEXT        NOT NULL,

    -- When the underlying data was valid (NOT the ingest timestamp)
    as_of_timestamp         TIMESTAMPTZ NOT NULL,

    -- Named entities present in this insight (e.g. ["Fresh Produce LLC", "Acme Corp"])
    entity_refs             JSONB       NOT NULL DEFAULT '[]',

    -- PipeHouse-assigned attribute labels (e.g. ["cogs_anomaly", "28pct_above_baseline"])
    attribute_tags          JSONB       NOT NULL DEFAULT '[]',

    -- Human-readable one-line title (max 120 chars)
    title                   TEXT        NOT NULL,

    -- Full insight description (used as bead content)
    content                 TEXT        NOT NULL,

    -- Why this insight was flagged (list of reason strings)
    because                 JSONB       NOT NULL DEFAULT '[]',

    -- Confidence in the insight [0.0, 1.0]; default 0.9
    confidence              REAL        NOT NULL DEFAULT 0.9,

    -- Optional: shared ID for cross-store joins (e.g. "meeting_2026-05-29_vendor-review")
    -- Set this when the insight relates to a meeting/call that also has a Core Memory
    -- transcript bead and/or a Ragie document. Use the same value on all three sides.
    -- Core Memory stores this in bead.links["core_memory_unifying_id"].
    -- Ragie documents store this in document_metadata["core_memory_unifying_id"].
    core_memory_unifying_id TEXT        DEFAULT NULL,

    -- Freeform PipeHouse-internal fields; passed through to bead.metadata
    pipehouse_metadata      JSONB       NOT NULL DEFAULT '{}',

    -- Set by Core Memory after successful ingest; NULL = not yet ingested
    -- Mode A (polling): Core Memory sets this after emit_turn_finalized succeeds
    -- Mode B (webhook): Core Memory sets this in the webhook handler
    ingested_at             TIMESTAMPTZ DEFAULT NULL,

    -- Core Memory bead ID assigned after ingest; NULL until ingested
    core_memory_bead_id     TEXT        DEFAULT NULL
);

-- Index for Mode A polling: efficiently find uningested rows ordered by data timestamp
CREATE INDEX idx_core_memory_insights_pending
    ON core_memory_insights (as_of_timestamp ASC)
    WHERE ingested_at IS NULL;

-- Index for unifying ID lookups at retrieval time
CREATE INDEX idx_core_memory_insights_unifying_id
    ON core_memory_insights (core_memory_unifying_id)
    WHERE core_memory_unifying_id IS NOT NULL;
