# ADR: Association Type Policy

Status: Accepted (updated)
Decision date: 2026-03-09

## Decision
Adopt policy: **`edge_primary_no_association_bead`**.

Meaning:
- associations are a separate class in the data model (association/edge records)
- `association` is not a canonical bead type
- bead creation with `type="association"` is rejected

## Rationale
- removes lingering ambiguity between bead ontology and relationship ontology
- aligns runtime/data model with target architecture
- keeps association logic where it belongs: association records / edge semantics

## Consequences
- canonical bead type set excludes `association`
- models/schema alignment reflects this removal
- tests enforce policy and rejection behavior
