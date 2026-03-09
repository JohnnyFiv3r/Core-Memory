# V2-P4 Closeout Checklist

Status: Complete
Related: `docs/v2_p4_kickoff.md`, `docs/v2_p4_test_matrix.md`

## Goal
Confirm surface/schema embodiment closure for V2-P4.

## Step progress
- [x] Step 1 of 5 — rolling surface contract hardening
- [x] Step 2 of 5 — rolling FIFO/token determinism expansion
- [x] Step 3 of 5 — association subsystem scaffold extraction
- [x] Step 4 of 5 — schema/model reconciliation
- [x] Step 5 of 5 — association type policy decision closure + full sweep

## Acceptance criteria
- [x] rolling surface has explicit contract metadata artifact
- [x] rolling selection policy deterministic under budget pressure
- [x] association pass has explicit subsystem entrypoint
- [x] model enums align with canonical schema sets
- [x] association type policy explicitly decided and documented

## Validation evidence

### Targeted P4 tests
- `tests.test_rolling_surface_contract`
- `tests.test_rolling_fifo_determinism`
- `tests.test_association_pass_contract`
- `tests.test_models_schema_alignment`
- `tests.test_association_type_policy`

### Full regression
- `python -m unittest discover -s tests -p 'test_*.py'`

### Eval snapshots
- `eval/memory_execute_eval.py`
- `eval/paraphrase_eval.py`

## Outcome
V2-P4 is complete. Rolling surface contract, association subsystem scaffold, schema alignment, and association type policy closure are all in place.

## Next phase
Proceed to V2-P5 (integration framing + legacy cleanup/deprecation resolution).
