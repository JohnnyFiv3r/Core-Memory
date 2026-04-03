# Shared Integration Concepts

Status: Canonical

## Core split: write vs retrieval vs hydration

### Write (ingestion)
Finalized-turn ingest appends durable memory events.

### Retrieval
Canonical runtime retrieval family:
- `search` (anchor retrieval)
- `trace` (causal/temporal traversal after anchor selection)
- `execute` (single orchestrated runtime entrypoint)

### Hydration
Hydration is explicit source recovery after retrieval selection.
It answers “show me the original turn/tool payload,” not “find memory.”

Deep recall is real, but separate from canonical hydration.

## Continuity vs retrieval
- **Continuity/context injection** helps keep near-term prompt context coherent.
- **Retrieval** finds durable, query-relevant memory objects.
- **Hydration** recovers raw evidence payloads for selected results.

## Why this distinction matters
Keeping these concerns separate avoids interface confusion and makes adapter behavior predictable.

## Determinism principles
- idempotent finalized-turn ingestion
- explicit grounding metadata
- explicit hydration request contract
- stable read/write boundaries
