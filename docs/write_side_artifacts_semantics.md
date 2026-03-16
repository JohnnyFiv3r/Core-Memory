# Write-Side Artifacts Semantics

Status: Canonical (Phase T5)
Purpose: classify write-side artifacts by memory surface role.

## Artifact classification

### `.beads/.extracted/session-<id>.json`
Surface role:
- write-side operational marker

Purpose:
- extraction idempotency and run bookkeeping

### Legacy trigger ledgers (retired)
- `.beads/events/write-triggers.jsonl`
- `.beads/events/write-trigger-processed.jsonl`

Status:
- retired with removal of legacy write-trigger dispatcher path.
- retained only as historical artifacts if present in older roots.

### `promoted-context.md`
Surface role:
- rolling window continuity artifact

Purpose:
- bounded continuity injection artifact

Not canonical for:
- exact historical specificity retrieval

### Session bead JSONL files
Surface role:
- session beads surface

Purpose:
- append-only per-session structured memory state

### Archive graph/index files
Surface role:
- archive graph surface

Purpose:
- durable historical retrieval and causal reasoning
