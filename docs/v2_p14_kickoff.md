# V2-P14 Kickoff (Worker Judgment Final Cut)

Status: Active

## Objective
Complete single-judgment authority by making semantic bead creation canonical through the agent-reviewed crawler path, with deterministic worker logic retained only as non-authoritative preview/compatibility support.

## Step plan (5)
1. Decision lock + canonical docs update ✅
2. Worker semantic creation demotion ✅
3. event_* import migration guardrails

## Step 2 completion notes
- Demoted worker semantic bead creation to preview-only mode in `core_memory.sidecar_worker`.
- Worker no longer creates canonical beads directly on turn processing.
- Worker now emits non-authoritative `creation_candidates` for agent/crawler-reviewed judgment flow.
- Updated authority tests to enforce no canonical bead creation mutation by worker.
4. Targeted invariants + matrix update
5. Sweep + closeout

## Step 1 completion notes
- Locked judgment authority in canonical docs:
  - semantic bead creation authority -> agent-reviewed crawler path
  - promotion/association authority -> agent-reviewed crawler path
  - deterministic worker outputs -> preview-only, non-authoritative
- Updated architecture and write-side canonical flow docs accordingly.
