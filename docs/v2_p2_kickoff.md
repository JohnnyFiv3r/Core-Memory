# V2-P2 Kickoff

Status: Active
Related: `docs/v2_execution_plan.md`

## Objective
Cut over write-side trigger authority to canonical in-process enforcement while preserving mainline path stability.

## Locked requirements
- documented per-turn trigger hooks must be enforced in canonical path
- sidecar remains compatibility only during migration
- flush hook is authoritative transaction boundary
- admin CLI flush trigger follows canonical path semantics

## Artifacts prepared
- `docs/v2_p2_trigger_map.md`
- `docs/v2_p2_enforcement_test_matrix.md`

## Implementation sequence (planned)
1. Introduce canonical trigger orchestration module (no contract changes) ✅
2. Route finalize/flush entrypoints to canonical trigger executor ✅
3. Keep sidecar as compatibility wrapper and add authority markers
4. Implement P2 matrix tests
5. Run full regression/eval and closeout gate

## Step 1 completion notes
- Added canonical orchestrator module: `core_memory/trigger_orchestrator.py`
- `finalize_and_process_turn(...)` now delegates to canonical orchestrator path
- Added regression coverage: `tests/test_trigger_orchestrator.py`
- Verified sidecar/openclaw integration compatibility tests remain passing

## Step 2 completion notes
- Added canonical flush trigger entrypoint: `run_flush_pipeline(...)` in `core_memory/trigger_orchestrator.py`
- `consolidate.py consolidate` now routes through canonical flush pipeline
- Added manual admin fail-safe command: `consolidate.py flush` (same canonical semantics)
- Added checkpoint ledger for flush stages: `.beads/events/flush-checkpoints.jsonl`
- Added regression coverage: `tests/test_trigger_orchestrator_flush.py`

## Stop condition
If any change destabilizes mainline path or causes contract drift, halt and revert to previous stable commit before proceeding.
