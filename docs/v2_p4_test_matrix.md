# V2-P4 Test Matrix

Status: Draft for execution
Related: `docs/v2_p4_kickoff.md`

## A) Rolling surface contract

1. `test_rolling_surface_has_explicit_contract_metadata`
- verifies rolling output includes deterministic projection metadata

2. `test_rolling_compression_scope_isolated`
- verifies rolling compression does not alter archive full-fidelity copy

3. `test_rolling_surface_bead_id_continuity`
- verifies rolling copy preserves bead IDs for represented beads

## B) FIFO / token-budget determinism

4. `test_rolling_fifo_recency_order`
- strict recency ordering under equal token conditions

5. `test_rolling_token_budget_cutoff_deterministic`
- deterministic inclusion/exclusion under budget pressure

6. `test_rolling_rebuild_replay_stability`
- repeated rolling rebuild yields stable output for unchanged source

## C) Association subsystem extraction

7. `test_association_pass_contract_shape`
- validates canonical pass input/output schema

8. `test_association_pass_deterministic_links`
- same input state yields same association outputs

9. `test_association_pass_non_destructive`
- derived association updates do not mutate unrelated bead fields

## D) Schema reconciliation

10. `test_models_schema_enum_alignment`
- validates `models.py` exposed enums align with canonical schema sets

11. `test_legacy_alias_normalization_still_works`
- regression for normalized legacy values

12. `test_status_set_alignment`
- ensures status values do not drift from canonical set

## E) Decision closure

13. `test_association_type_policy_enforced`
- verifies chosen policy for association bead-type vs edge-type is enforced

## Exit threshold
- All P4 matrix tests passing
- Existing full regression remains green
- eval metrics non-regressed vs P3 closeout snapshot
