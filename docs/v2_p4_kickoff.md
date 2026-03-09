# V2-P4 Kickoff

Status: Active
Related: `docs/v2_phase_ticket_map.md`, `docs/v2_gap_checklist.md`

## Objective
Close surface and schema embodiment gaps:
- rolling surface as first-class continuity store contract
- association subsystem extraction
- schema/model reconciliation
- association bead-type decision closure

## Step plan (5)
1. Rolling window first-class surface contract hardening ✅
2. Rolling FIFO/token-budget determinism test expansion ✅
3. Association subsystem extraction scaffold ✅
4. Schema reconciliation (`models.py` aligned to canonical `schema.py`) ✅
5. Association bead-type decision closure + P4 closeout ✅

## Planned outputs
- `docs/v2_p4_surface_contract.md`
- `docs/v2_p4_test_matrix.md`
- `core_memory/association/` scaffold (step 3)
- schema reconciliation diff + migration note (step 4)
- ADR/decision note for association bead-type resolution (step 5)

## Guardrails
- Preserve mainline path stability at each step.
- No silent contract drift in `memory.execute/search/reason`.
- Keep compatibility wrappers until explicit deprecation step.

## Step 1 completion notes
- Hardened rolling surface contract metadata in `core_memory/write_pipeline/window.py`
- Rolling metadata now includes explicit surface contract fields:
  - `surface: rolling_window`
  - `selection_policy: strict_recency_fifo_with_budget`
  - `compression_scope: rolling_only`
- Added first-class rolling surface metadata artifact:
  - `promoted-context.meta.json`
- Propagated metadata/id sets from write-pipeline consolidate/refresh paths
- Added regression coverage:
  - `tests/test_rolling_surface_contract.py`
  - extended `tests/test_write_pipeline_consolidate_parity.py`

## Step 2 completion notes
- Enforced strict FIFO budget cutoff behavior in rolling builder:
  - once budget boundary is hit, stop (do not skip forward to older beads)
- Added deterministic rolling behavior tests:
  - `tests/test_rolling_fifo_determinism.py`
    - recency order verification
    - strict budget cutoff behavior
    - rebuild stability for same source state

## Step 3 completion notes
- Added association subsystem scaffold package:
  - `core_memory/association/__init__.py`
  - `core_memory/association/pass_engine.py`
- Introduced canonical association pass entrypoint:
  - `run_association_pass(index, bead, max_lookback, top_k)`
- Rewired store quick-association path to use association subsystem pass
  - `MemoryStore._quick_association_candidates(...)` now delegates to `run_association_pass(...)`
- Added association pass contract tests:
  - `tests/test_association_pass_contract.py`
  - validates deterministic output, contract shape, and non-destructive behavior

## Step 4 completion notes
- Reconciled model enums with canonical schema vocabulary in `core_memory/models.py`:
  - `BeadType` now includes canonical `context` and `correction`
  - `Status` removed non-canonical `closed`
  - `RelationshipType` aligned to canonical relation set (including `supports`, `derived_from`, `resolves`, `follows`)
- Added schema-alignment regression tests:
  - `tests/test_models_schema_alignment.py`
  - asserts exact enum-set equality with canonical schema constants

## Step 5 completion notes
- Closed association type policy decision via explicit ADR:
  - `docs/adr_association_type_policy.md`
- Policy selected: `keep_as_bead_and_edge`
  - keep association bead type for compatibility/history
  - continue edge semantics for association relations
- Added explicit policy constant and accessor in schema module:
  - `ASSOCIATION_TYPE_POLICY`
  - `association_policy()`
- Added policy enforcement test:
  - `tests/test_association_type_policy.py`
- Authored phase closeout artifact:
  - `docs/v2_p4_closeout_checklist.md`
