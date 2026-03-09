# ADR: Association Type Policy

Status: Superseded by P6B Step 2 update
Original decision date: 2026-03-09
Updated decision date: 2026-03-09

## Context
V2 architecture reviews flagged ambiguity around whether `association` should remain a bead type or be represented only as edge semantics.

## Updated Decision
Adopt policy: **`edge_primary_explicit_bead_only`**.

Meaning:
- `association` remains in canonical bead types for explicit/audit use only.
- Association behavior for derived linking is edge-primary.
- Implicit association bead creation is disallowed by default.
- Compatibility override exists via `CORE_MEMORY_ALLOW_IMPLICIT_ASSOCIATION_BEAD=1`.

## Rationale
- Move decisively toward target architecture while preserving a controlled compatibility escape hatch.
- Reduce architectural ambiguity between edge records and bead records.
- Keep existing stored data valid without forcing immediate destructive migration.

## Consequences
- Schema policy constant updated to `edge_primary_explicit_bead_only`.
- Store enforces explicit flag for association bead creation by default.
- Tests cover policy enforcement and compatibility override behavior.
