# V2-P0 Kickoff (Baseline + Execution Readiness)

Status: Complete
Related: `docs/v2_execution_plan.md`, `docs/transition_roadmap_v2.md`

## Purpose
Establish a locked baseline before V2 implementation phases.

## P0 checklist
- [x] Confirm V2 architecture docs exist and are indexed
- [x] Confirm non-negotiable isolation from OpenClaw `MEMORY.md`
- [x] Confirm per-turn trigger enforcement requirement is documented in V2-P2
- [x] Capture full regression baseline
- [x] Capture eval baseline

## Baseline results

### Test baseline
- Command: `python -m unittest discover -s tests -p 'test_*.py'`
- Result: **144 passed / 0 failed**

### Eval baseline: memory_execute
- count: 50
- ok_rate: 1.0
- non_empty_results_rate: 1.0
- confidence_high_rate: 0.88
- confidence_medium_rate: 0.12
- answerable_rate: 1.0
- causal_grounding_achieved_rate: 1.0
- causal_strong_grounding_rate: 0.8421
- surface_alignment_rate: 1.0
- durable_query_archive_hit_rate: 0.84
- ungrounded_causal_surface_mismatch_rate: 0.0

### Eval baseline: paraphrase
- paraphrase_consistency_at_5: 0.085
- paraphrase_consistency_at_10: 0.17
- anchor_hit_rate: 0.85
- intent_match_rate: 0.7
- non_causal_why_rate: 0.0

## Next step
Proceed to **V2-P1** (canonical spec lock artifacts):
- `docs/v2_invariants.md`
- `docs/v2_flush_transaction_spec.md`
- `docs/v2_surface_authority_matrix.md`
- `docs/v2_deprecation_inventory.md`
