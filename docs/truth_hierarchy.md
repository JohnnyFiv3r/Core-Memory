# Truth Hierarchy / Memory Surfaces

Status: Canonical

## Live session truth
1. `.beads/session-<id>.jsonl` (live authority)
2. Optional compatibility fallback: index projection (only when explicitly enabled)

## Continuity injection truth
1. `rolling-window.records.json` (authority)
2. `promoted-context.meta.json` (fallback metadata only)
3. `promoted-context.md` (derived/operator artifact only)

## Retrieval truth
- Structured historical retrieval resolves against canonical archive/graph/projection surfaces via typed retrieval pipeline.

## MEMORY.md boundary
`MEMORY.md` is an OpenClaw parallel surface and is not a canonical Core Memory runtime/storage truth source.
