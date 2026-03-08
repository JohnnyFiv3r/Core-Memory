# Memory Surfaces Spec

Status: Canonical (Phase T5)
Purpose: define the distinct memory surfaces and their authoritative roles.

## Surface A: Transcript
Purpose:
- immediate, verbatim turn history

Authoritative for:
- exact recent wording
- same-session immediate context

Not authoritative for:
- durable historical project memory

## Surface B: Session Beads
Purpose:
- live structured per-session memory units

Authoritative for:
- current session structured state
- per-turn association growth

Not authoritative for:
- full historical retrieval across long horizons

## Surface C: Rolling Window
Purpose:
- continuity injection artifact under bounded prompt budget

Authoritative for:
- continuity hints
- causal awareness carry-forward

Not authoritative for:
- specific historical fact retrieval

## Surface D: Archive Graph
Purpose:
- full-fidelity historical retrieval + causal chain support

Authoritative for:
- durable memory retrieval
- causal reasoning over historical structure

Not authoritative for:
- immediate verbatim turn truth in an active session

## Surface E: MEMORY.md (OpenClaw-parallel)
Purpose:
- OpenClaw semantic summary memory

Authoritative for:
- OpenClaw-specific semantic summary behavior

Not authoritative for:
- canonical structured Core Memory bead/graph truth

Boundary rule:
- Core Memory must not read, write, index, or depend on `MEMORY.md`.
- This surface remains parallel/complementary to Core Memory, not part of Core Memory runtime/storage paths.
