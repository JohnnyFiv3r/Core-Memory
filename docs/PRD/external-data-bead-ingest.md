# PRD: External Data Bead Ingest Contract

**Status:** Implemented — superseded by the generic typed external-evidence ingest contract
**Effort:** Historical estimate; implementation shipped through the external-evidence path
**Originally blocked:** #15 (PipeHouse adapter in multi-store fan-out)
**Original integration partner:** PipeHouse (Data Pipeline Builders)

---

## Current implementation note

This PRD's original PipeHouse-specific `data_insight` table/polling design has
been superseded by the shipped, product-neutral typed external-evidence path.
Core Memory now writes source-attributed external anchors through
`core_memory.runtime.ingest.external_evidence`, with source-envelope normalization
in `core_memory.runtime.ingest.source_envelope`.

Current public write surfaces:

- `POST /v1/memory/external-evidence`
- `POST /v1/memory/structured-observation`
- `POST /v1/memory/document-reference`
- `POST /v1/memory/state-assertion`

Current canonical source-backed bead types include `transcript`,
`document_reference`, `structured_observation`, `state_assertion`,
`operational_event`, and `data_insight`. The shipped contract preserves
`source_id`, `source_event_id`, `source_system`, `source_kind`, `source_ref`,
`source_refs`, `source_attribution`, `hydration_ref`,
`core_memory_unifying_id`, typed source fields such as `source_table` and
`source_record_id`, immutable source-version receipts, and association coverage
triggering after durable write.

The historical design below is retained for provenance. Where it conflicts with
the shipped generic external-evidence contract, the shipped contract is
authoritative.

---

## Historical problem

At the time this PRD was written, there was no defined contract for how
relational data insights from an external system entered Core Memory's write
pipeline. The agreed model — PipeHouse normalizes data, writes insights to a
table, Core Memory reads and generates a bead — had no schema, ingest path, or
specified bead type. PipeHouse had nothing concrete to build against. The causal
edges drawn in the External Memory Runtime architecture diagram (`data supports
decision`, `data led_to investigation`) had no data to anchor to.

---

## User value

- Relational data insights (COGS anomalies, revenue spikes, pipeline metrics) appear as
  bead-level evidence alongside conversation beads in recall results.
- Data insights participate in agent-judged association crawling — the same `supports`,
  `led_to`, and `caused_by` edges that connect conversation turns can connect a data
  anomaly to the decision it prompted.
- Full provenance: every data bead is traceable back to its source table, source record
  ID, and the timestamp at which the data was valid (`as_of_timestamp`), not just when
  it was ingested.

---

## Current implementation state

| Component | Status |
|-----------|--------|
| `"data_insight"` bead type | Shipped in `BeadType`; relational evidence usually uses `structured_observation`, while `data_insight` remains a canonical external type |
| DB table schema for PipeHouse writes | Superseded by caller-owned source stores plus the typed external-evidence payload contract |
| Ingest path (polling or webhook) | Shipped as `runtime/ingest/external_evidence.py` and HTTP endpoints under `/v1/memory/*` |
| `core_memory_unifying_id` convention | Shipped; required for non-`state_assertion` external evidence payloads |
| Association eligibility for data beads | Shipped via post-write association coverage after durable external-evidence ingest |

---

## Success criteria

The criteria below describe the original PipeHouse-specific design. The shipped
contract satisfies the source-attributed bead and association-eligibility goals
through the generic external-evidence path described above, not through a
Core-Memory-owned PipeHouse polling table.

1. A PipeHouse insight row written to the agreed DB table is ingested into Core Memory
   as a `type: "data_insight"` bead within the polling interval (Mode A) or within
   5 seconds (Mode B webhook).
2. The bead carries `source_table`, `source_record_id`, `as_of_timestamp`,
   `entity_refs`, `attribute_tags`, and `content` in its structured fields.
3. `bead.links["external_source_id"]` holds the PipeHouse `record_id` so the
   #15 fan-out adapter can join on it.
4. The ingest path calls `emit_turn_finalized()` — no direct persistence writes.
5. A `data_insight` bead participates in the standard association crawler without
   special-casing: the crawler sees it as a normal bead and may produce `supports`,
   `led_to`, or `caused_by` associations to conversation beads.
6. Mode A (polling): ingested rows have `ingested_at` set on the source table so
   subsequent polls skip them.
7. Mode B (webhook): `POST /api/ingest/data-insight` returns 200 with the new
   `bead_id` on success, 400 on schema validation failure, 500 on ingest error.

---

## Scope

**In:**
- `"data_insight"` added to `BeadType` enum (`schema/models.py`)
- DB table schema (see below) — the interface PipeHouse writes to
- `runtime/ingest/data_insight.py` — row → turn envelope → `emit_turn_finalized`
- Mode A: `"data-insight-poll"` job kind in `runtime/jobs.py`
- Mode B: `POST /api/ingest/data-insight` endpoint in `demo/app.py`
- `external_source_id` convention documented and enforced at ingest

**Out:**
- PipeHouse writing directly to Core Memory's bead store — all writes go through
  `emit_turn_finalized()`
- The ingest module living in `integrations/` — it belongs in `runtime/ingest/`
- Retroactive re-ingestion of historical PipeHouse rows
- LLM-based attribute classification — PipeHouse supplies tags; Core Memory accepts them
- Association creation at ingest time — associations are always agent-judged at `agent_end`

---

## Bead schema for `data_insight` type

A `data_insight` bead is a standard Core Memory bead with the following required fields:

```json
{
  "type": "data_insight",
  "title": "<human-readable summary, max 120 chars>",
  "content": "<full insight description>",
  "source_system": "pipehouse",
  "source_table": "<originating table name>",
  "source_record_id": "<pipehouse row primary key>",
  "as_of_timestamp": "<iso8601 — when the underlying data was valid>",
  "entity_refs": ["<entity name>"],
  "attribute_tags": ["<tag>"],
  "links": {
    "external_source_id": "<source_record_id — repeated for query convenience>"
  }
}
```

**Optional fields:**
```json
{
  "core_memory_unifying_id": "<shared ID for cross-store joins, e.g. meeting_2026-05-29>",
  "confidence": 0.9,
  "because": ["<reason the insight was flagged>"]
}
```

The `as_of_timestamp` is the data timestamp (when the metric was measured), not the
ingest timestamp. This distinction matters for temporal recall (`#13 as_of` queries).

---

## DB table schema (PipeHouse writes here; Core Memory reads here)

```sql
CREATE TABLE core_memory_insights (
    id                    TEXT        PRIMARY KEY,
    source_table          TEXT        NOT NULL,
    as_of_timestamp       TIMESTAMPTZ NOT NULL,
    entity_refs           JSONB       NOT NULL DEFAULT '[]',
    attribute_tags        JSONB       NOT NULL DEFAULT '[]',
    title                 TEXT        NOT NULL,
    content               TEXT        NOT NULL,
    because               JSONB       NOT NULL DEFAULT '[]',
    confidence            REAL        NOT NULL DEFAULT 0.9,
    core_memory_unifying_id TEXT      DEFAULT NULL,
    pipehouse_metadata    JSONB       NOT NULL DEFAULT '{}',
    ingested_at           TIMESTAMPTZ DEFAULT NULL
);

CREATE INDEX ON core_memory_insights (ingested_at)
    WHERE ingested_at IS NULL;
```

**Column notes:**
- `id` — PipeHouse-assigned primary key; maps to `source_record_id` on the bead
- `ingested_at` — NULL until Core Memory ingests the row; set by the ingest job (Mode A)
  or webhook handler (Mode B) after successful `emit_turn_finalized()`
- `core_memory_unifying_id` — optional; set when the insight relates to a specific
  meeting, video, or event that also has a Core Memory transcript bead
- `pipehouse_metadata` — PipeHouse-internal fields; passed through to `bead.metadata`
  but not used by Core Memory for any logic

**Core Memory reads:**
```sql
SELECT * FROM core_memory_insights
WHERE ingested_at IS NULL
ORDER BY as_of_timestamp ASC
LIMIT 50;
```

---

## Ingest path: `runtime/ingest/data_insight.py`

```python
def ingest_data_insight_row(root: str, session_id: str, row: dict) -> dict:
    """
    Convert a core_memory_insights row to a turn envelope and call
    emit_turn_finalized(). Returns {"ok": True, "bead_id": "..."} on success.

    Row must have: id, source_table, as_of_timestamp, entity_refs,
    attribute_tags, title, content.
    """
```

The turn envelope constructed by this function:

```python
{
    "session_id": session_id,
    "turn_id": f"data-insight-{row['id']}",
    "turns": [{
        "role": "system",
        "content": row["content"],
        "metadata": {
            "type": "data_insight",
            "source_system": "pipehouse",
            "source_table": row["source_table"],
            "source_record_id": row["id"],
            "as_of_timestamp": row["as_of_timestamp"],
            "entity_refs": row["entity_refs"],
            "attribute_tags": row["attribute_tags"],
            "title": row["title"],
        }
    }],
    "origin": "pipehouse",
}
```

---

## Mode A: Polling job (`runtime/jobs.py`)

Add a `"data-insight-poll"` job kind:

```python
{
    "kind": "data-insight-poll",
    "interval_seconds": 60,
    "batch_size": 50,
    "db_url_env": "CORE_MEMORY_PIPEHOUSE_DB_URL",
    "session_id_env": "CORE_MEMORY_PIPEHOUSE_SESSION_ID",
}
```

The job:
1. Reads up to `batch_size` uningested rows from `core_memory_insights`
2. Calls `ingest_data_insight_row()` for each
3. On success, sets `ingested_at = NOW()` on the source row
4. On failure, logs the error and skips the row (retried next poll)

Configured via `CORE_MEMORY_PIPEHOUSE_DB_URL` env var. If unset, job is a no-op.

---

## Mode B: Webhook endpoint (`demo/app.py`)

```
POST /api/ingest/data-insight
Content-Type: application/json

{
    "id": "<record id>",
    "source_table": "<table>",
    "as_of_timestamp": "<iso8601>",
    "entity_refs": ["<entity>"],
    "attribute_tags": ["<tag>"],
    "title": "<title>",
    "content": "<content>",
    "core_memory_unifying_id": "<optional>",
    "confidence": 0.9,
    "because": [],
    "pipehouse_metadata": {}
}

Response 200: { "ok": true, "bead_id": "<new bead id>" }
Response 400: { "ok": false, "error": "<validation message>" }
Response 500: { "ok": false, "error": "<ingest error>" }
```

The endpoint calls `ingest_data_insight_row()` directly. No polling table involved
in Mode B — PipeHouse manages its own record state.

---

## Unifying ID convention

When a PipeHouse insight relates to a meeting, Zoom call, or other event that also
has a Core Memory transcript bead, both sides carry the same `core_memory_unifying_id`:

- PipeHouse row: `core_memory_unifying_id = "meeting_2026-05-29_vendor-review"`
- Core Memory transcript bead: `bead.links["core_memory_unifying_id"] = "meeting_2026-05-29_vendor-review"`

The #15 fan-out layer uses this ID to group related items at retrieval time.
The ID format is caller-determined; Core Memory treats it as an opaque string.

---

## Implementation tasks

1. **`schema/models.py`** — Add `DATA_INSIGHT = "data_insight"` to `BeadType` enum.
   Add `DATA_INSIGHT` to `CLASSIFIABLE_TYPES` in `policy/bead_typing.py` if it should
   be auto-classified from turn content (recommend: no — it is always explicitly typed
   by the ingest path, not inferred).

2. **`runtime/ingest/__init__.py`** — Create `runtime/ingest/` subpackage (if it does
   not exist — verify). Keep it empty; the subpackage is the namespace.

3. **`runtime/ingest/data_insight.py`** — Implement `ingest_data_insight_row()` per
   contract above. Validate required fields at entry; raise `ValueError` on missing
   fields so callers get explicit errors, not silent empty beads.

4. **`runtime/jobs.py`** — Add `"data-insight-poll"` job kind. Read
   `CORE_MEMORY_PIPEHOUSE_DB_URL` from env. If unset, register the job as a no-op (do not
   error on startup).

5. **`demo/app.py`** — Add `POST /api/ingest/data-insight` handler. Validate body,
   call `ingest_data_insight_row()`, return `bead_id` or error.

6. **DB migration** — Provide the `CREATE TABLE` statement above as a committed
   artifact at `docs/schema/pipehouse_insights_table.sql` for Chris to implement on
   the PipeHouse side.

7. **Tests** — Three fixtures:
   - Valid row → bead created with correct type and fields
   - Row missing required field → `ValueError` raised, no bead written
   - Duplicate `id` (same row submitted twice) → second call is idempotent (no duplicate bead)

---

## Dependencies / risks

- **`emit_turn_finalized` session coupling:** The ingest path requires a `session_id`.
  For Mode A (polling), a dedicated `CORE_MEMORY_PIPEHOUSE_SESSION_ID` is configured as a
  long-lived data session ID. For Mode B (webhook), the caller supplies it. Both are
  valid — data beads accumulate in a persistent data session, not an ephemeral agent
  session.
- **`runtime/ingest/` may not exist as a subpackage.** Verify before creating the file;
  if the namespace conflicts with existing code, use `runtime/data_ingest.py` instead.
- **DB connection management:** The polling job needs a DB connection to the PipeHouse
  table. Do not add `psycopg2` or `sqlalchemy` as a hard dependency — use the optional
  import pattern already established for other optional backends. If the dep is not
  installed, the polling job logs a warning and skips.
- **Association timing:** Data beads enter the association crawler at `agent_end` on the
  next turn that runs in the same session. If the data session is isolated (no agent
  turns), associations are never crawled. Acceptable for v1 — document the limitation.
