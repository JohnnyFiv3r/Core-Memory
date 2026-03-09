# ADR: Association Type Policy

Status: Accepted (V2-P4 Step 5)
Decision date: 2026-03-09

## Context
V2 architecture reviews flagged ambiguity around whether `association` should remain a bead type or be represented only as edge semantics.

## Decision
Adopt policy: **`keep_as_bead_and_edge`** during current transition.

Meaning:
- `association` remains an allowed bead type for backward compatibility and historical continuity.
- Association relationships remain represented through edge/relation semantics.
- New association subsystem passes should prefer derived edge-style association semantics for incremental linkage behavior.

## Rationale
- Avoid disruptive migration while V2 core runtime/storage hardening is still in progress.
- Preserve compatibility with existing stored data and tests.
- Keep a clean forward path for possible future deprecation of bead-type association once migration tooling and observability are stronger.

## Consequences
- Schema continues to allow `association` bead type.
- Policy is now explicit and testable.
- Future V2/P5+ work can revisit with migration plan if deprecation is desired.
