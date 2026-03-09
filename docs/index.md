# Documentation Index

Status: Canonical

Start here for current Core Memory documentation.

## Architecture and canonical interfaces
- `canonical_surfaces.md` — current supported public surfaces
- `core_adapters_architecture.md` — integration architecture overview
- `contracts/http_api.v1.json` — canonical HTTP/API contract artifact
- `transition_roadmap_locked.md` — locked transition execution roadmap
- `phase1_closeout_checklist.md` — phase 1 readiness checklist
- `schema_inventory_baseline.md` — phase 2 schema baseline
- `schema_canonical_spec.md` — canonical schema specification
- `phase2_closeout_checklist.md` — phase 2 readiness checklist
- `phase3_trigger_model_progress.md` — phase 3 trigger-model convergence progress
- `phase3_closeout_checklist.md` — phase 3 readiness checklist
- `phase4_internalization_progress.md` — phase 4 internalization progress
- `phase4_closeout_checklist.md` — phase 4 readiness checklist
- `memory_surfaces_spec.md` — canonical memory surfaces specification
- `truth_hierarchy_policy.md` — deterministic truth hierarchy policy
- `write_side_artifacts_semantics.md` — write-side artifact surface semantics
- `phase5_closeout_checklist.md` — phase 5 readiness checklist
- `phase6_runtime_hardening_progress.md` — phase 6 runtime hardening progress
- `runtime_contract_clarity.md` — runtime confidence/next_action/grounding semantics
- `phase6_closeout_checklist.md` — phase 6 readiness checklist
- `adapter_layer_inventory.md` — adapter vs canonical semantics clarification
- `transition_roadmap_v2.md` — superseding roadmap draft aligned to event-native write authority
- `v2_execution_plan.md` — phased execution plan for V2 cutover and legacy deprecation
- `v2_p0_kickoff.md` — V2 baseline and execution readiness snapshot
- `v2_invariants.md` — non-negotiable V2 architecture and behavior invariants
- `v2_flush_transaction_spec.md` — staged idempotent flush transaction specification
- `v2_surface_authority_matrix.md` — canonical authority/role matrix across memory surfaces
- `v2_deprecation_inventory.md` — legacy path deprecation/removal inventory for V2
- `v2_p2_kickoff.md` — V2-P2 kickoff and implementation sequence
- `v2_p2_trigger_map.md` — canonical trigger authority map for per-turn + flush paths
- `v2_p2_enforcement_test_matrix.md` — required enforcement tests for P2 closeout
- `v2_p2_closeout_checklist.md` — V2-P2 completion and validation gate
- `v2_gap_checklist.md` — realized/partial/missing/misaligned architecture gap assessment
- `v2_phase_ticket_map.md` — mapped execution tickets for V2-P3/P4/P5
- `v2_p3_kickoff.md` — V2-P3 kickoff and step tracker
- `v2_p3_closeout_checklist.md` — V2-P3 completion and resilience validation gate
- `v2_p4_kickoff.md` — V2-P4 kickoff and step tracker
- `v2_p4_test_matrix.md` — V2-P4 rolling/association/schema validation matrix
- `adr_association_type_policy.md` — association bead-type policy decision record
- `v2_p4_closeout_checklist.md` — V2-P4 completion and validation gate
- `v2_p5_kickoff.md` — V2-P5 kickoff and step tracker
- `v2_p5_integration_inventory.md` — integration framing inventory and target map
- `v2_p5_legacy_classification.md` — canonical/compat/deprecated classification matrix
- `v2_p5_closeout_checklist.md` — V2-P5 completion and validation gate
- `v2_legacy_resolution_summary.md` — resolved/deprecated legacy path summary snapshot
- `v2_p6a_kickoff.md` — P6A authority-cutover kickoff and step plan
- `v2_p6a_test_matrix.md` — P6A authority-cutover validation matrix
- `v2_p6b_kickoff.md` — P6B semantic-closure kickoff and step plan
- `v2_p6b_test_matrix.md` — P6B semantic-closure validation matrix

## Integration guides
- `integrations/springai/quickstart.md` — SpringAI integration start point
- `integrations/openclaw/quickstart.md` — OpenClaw integration start point
- `integrations/pydanticai/quickstart.md` — PydanticAI integration start point
- `integration/core-adapters.md` — adapter overview across orchestrators (supporting)
- `memory_search_skill.md` — memory skill runtime surface
- `memory_search_agent_playbook.md` — agent-side usage guidance

## Validation and evaluation
- `../eval/memory_execute_eval.py`
- `../eval/memory_search_ab_compare.py`
- `../eval/memory_search_smoke.py`
- `../eval/paraphrase_eval.py`
- `../eval/retrieval_eval.py`

## Historical / snapshot material
- `archive/` — archived migration/deprecation/history docs
- dated `*_2026-03-05.md` reports in `docs/` — point-in-time evaluation artifacts

## Suggested reading order for contributors
1. `canonical_surfaces.md`
2. `contracts/http_api.v1.json`
3. relevant integration quickstart under `integrations/`
4. validation/eval scripts if changing behavior
