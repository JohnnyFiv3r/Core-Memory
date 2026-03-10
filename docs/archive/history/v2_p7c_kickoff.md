# V2-P7C Kickoff (Final Cleanup / Shim Retirement)

Status: Active

## Step plan (5)
1. Shim inventory + explicit deprecation markers ✅
2. Compatibility path usage audit + migration map ✅
3. Low-risk shim retirements (where no callers remain) ✅
4. Canonical-path-only docs finalization ✅
5. Full sweep + P7C closeout ✅

## Step 1 completion notes
- Added explicit `LEGACY_SHIM` / `SHIM_REPLACEMENT` markers to:
  - `core_memory.memory_skill.form`
  - `core_memory.write_pipeline.window`
- Authored shim inventory artifact:
  - `docs/v2_p7c_shim_inventory.md`
- Added shim marker regression test:
  - `tests/test_p7c_shim_markers.py`

## Step 2 completion notes
- Performed compatibility path usage audit across core code/tests/docs.
- Authored migration map artifact:
  - `docs/v2_p7c_usage_audit.md`
- Identified low-risk Step 3 retirements:
  - migrate remaining internal callsites to primary modules
  - retain shim modules as compatibility shells in this phase

## Step 3 completion notes
- Migrated internal rolling callsites to canonical primary module:
  - `core_memory.rolling_surface`
- Updated internal/test imports that previously depended on `write_pipeline.window` shim.
- Preserved shim module for compatibility (`write_pipeline.window`) with explicit deprecation marker.
- Verified shim marker test and primary-path tests remain green.

## Step 4 completion notes
- Added canonical-paths reference doc:
  - `docs/canonical_paths.md`
- Updated shared integration canonical sources to include canonical-path reference.
- Consolidated docs posture around primary authority modules and compatibility shim boundaries.

## Step 5 completion notes
- Ran full regression suite: `193 passed / 0 failed`
- Ran eval snapshots and confirmed stable metrics:
  - `memory_execute_eval`
  - `paraphrase_eval`
- Authored closeout artifacts:
  - `docs/v2_p7c_closeout_checklist.md`
  - `docs/v2_program_closeout.md`

## Objective
Reduce remaining transitional seams by making shim/deprecated paths explicit and retiring safe ones without breaking mainline behavior.
