# V2-P10 Kickoff (Strict Cleanup Pass)

Status: Active

## Objective
Reduce transitional drift by removing safe shim files now, then tightening deprecation and archive boundaries in follow-up steps.

## Step plan (4)
1. Immediate removals + import/test cleanup ✅
2. Transitional deprecation marking pass
3. Docs archive and surface cleanup
4. Sweep + closeout

## Step 1 completion notes
- Removed shim files:
  - `core_memory/memory_skill/form.py`
  - `core_memory/write_pipeline/window.py`
- Removed obsolete shim-marker test:
  - `tests/test_p7c_shim_markers.py`
- Updated primary search-form contract test to validate canonical module constants directly.
- Updated canonical-path docs to remove direct window-shim reference.
