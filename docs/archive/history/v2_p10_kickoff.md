# V2-P10 Kickoff (Strict Cleanup Pass)

Status: Active

## Objective
Reduce transitional drift by removing safe shim files now, then tightening deprecation and archive boundaries in follow-up steps.

## Step plan (4)
1. Immediate removals + import/test cleanup ✅
2. Transitional deprecation marking pass ✅
3. Docs archive and surface cleanup ✅
4. Sweep + closeout ✅

## Step 4 completion notes
- Completed V2P10 cleanup regression sweep.
- Added closeout artifact:
  - `docs/v2_p10_closeout_checklist.md`
- Sweep result: 20 passed / 0 failed.
- V2P10 is now closed.

## Step 1 completion notes
- Removed shim files:
  - `core_memory/memory_skill/form.py`
  - `core_memory/write_pipeline/window.py`
- Removed obsolete shim-marker test:
  - `tests/test_p7c_shim_markers.py`
- Updated primary search-form contract test to validate canonical module constants directly.
- Updated canonical-path docs to remove direct window-shim reference.

## Step 2 completion notes
- Marked transitional/deprecation intent explicitly in code for active compatibility modules:
  - `core_memory.association.pass_engine` (legacy-primary marker + replacement target)
  - `extract-beads.py` (legacy/backfill runtime note)
  - `core_memory.write_pipeline.*` legacy transcript/backfill modules (module doc markers)
- Added regression for deprecation markers:
  - `tests/test_p10_deprecation_markers.py`

## Step 3 completion notes
- Archived dated point-in-time report snapshots from primary docs surface to:
  - `docs/archive/reports/2026-03-05/`
- Updated `docs/index.md` to reference archived report location.
- Added current-program surface references (P8/P9/P10) to the main index.
