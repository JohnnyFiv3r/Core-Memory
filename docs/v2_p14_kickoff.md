# V2-P14 Kickoff (Worker Judgment Final Cut)

Status: Active

## Objective
Complete single-judgment authority by making semantic bead creation canonical through the agent-reviewed crawler path, with deterministic worker logic retained only as non-authoritative preview/compatibility support.

## Step plan (5)
1. Decision lock + canonical docs update ✅
2. Worker semantic creation demotion ✅
3. event_* import migration guardrails ✅
4. Targeted invariants + matrix update ✅
5. Sweep + closeout

## Step 4 completion notes
- Expanded pre-OSS matrix to include V2P13/V2P14 authority and import-migration invariants:
  - `tests.test_p13_authority_enforcement`
  - `tests.test_event_import_migration_guard`
  - `tests.test_event_module_aliases`
- Updated matrix manifest and one-command runner:
  - `tests/test_pre_oss_matrix.py`
  - `scripts/run_pre_oss_matrix.sh`
- Validation result: 30 passed / 0 failed.

## Step 1 completion notes
- Locked judgment authority in canonical docs:
  - semantic bead creation authority -> agent-reviewed crawler path
  - promotion/association authority -> agent-reviewed crawler path
  - deterministic worker outputs -> preview-only, non-authoritative
- Updated architecture and write-side canonical flow docs accordingly.

## Step 2 completion notes
- Demoted worker semantic bead creation to preview-only mode in `core_memory.sidecar_worker`.
- Worker no longer creates canonical beads directly on turn processing.
- Worker now emits non-authoritative `creation_candidates` for agent/crawler-reviewed judgment flow.
- Updated authority tests to enforce no canonical bead creation mutation by worker.

## Step 3 completion notes
- Added guardrail test to prevent drift back to legacy sidecar imports in core runtime modules:
  - `tests/test_event_import_migration_guard.py`
- Allowed sidecar references are now constrained to sidecar compatibility modules and canonical `event_*` aliases.
- Confirms migration path remains `event_ingress` / `event_worker` / `event_state` for runtime-facing code.
