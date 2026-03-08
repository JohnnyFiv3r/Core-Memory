# V2-P2 Enforcement Test Matrix

Status: Draft for implementation
Related: `docs/v2_p2_trigger_map.md`

## Purpose
Define the minimum test set required to declare V2-P2 complete.

## A) Per-turn trigger enforcement

1. `test_trigger_per_turn_finalized_runs_in_canonical_path`
- verifies canonical in-process per-turn hook executes

2. `test_trigger_per_turn_idempotent_replay`
- same envelope hash replay does not duplicate writes

3. `test_trigger_per_turn_mutation_path`
- changed envelope for same turn follows mutation/supersedes flow

4. `test_trigger_per_turn_ordering`
- verifies deterministic action ordering: write -> promote -> associate -> tag -> candidate-eval -> checkpoint

## B) Flush trigger authority

5. `test_flush_trigger_requires_final_turn_enriched`
- flush fails/defers if final-turn trigger incomplete

6. `test_flush_trigger_uses_canonical_transaction_path`
- flush hook invokes canonical staged path, not legacy-only route

7. `test_admin_flush_invokes_same_canonical_path`
- manual admin trigger shares identical semantics

## C) Sidecar downgrade/compatibility

8. `test_sidecar_path_marked_legacy_not_authority`
- compatibility behavior preserved but authority markers indicate non-canonical

9. `test_no_dual_authority_conflict`
- sidecar + canonical triggers do not produce double-processing

## D) Safety and observability

10. `test_trigger_checkpoint_written`
- completion checkpoints emitted for per-turn and flush stages

11. `test_trigger_diagnostics_stable_order`
- warnings/diagnostics deterministic in output ordering

## Exit threshold
- All matrix tests passing
- Existing regression suite remains green
- No contract drift in execute/search/reason outputs
