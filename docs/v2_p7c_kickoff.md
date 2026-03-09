# V2-P7C Kickoff (Final Cleanup / Shim Retirement)

Status: Active

## Step plan (5)
1. Shim inventory + explicit deprecation markers ✅
2. Compatibility path usage audit + migration map
3. Low-risk shim retirements (where no callers remain)
4. Canonical-path-only docs finalization
5. Full sweep + P7C closeout

## Step 1 completion notes
- Added explicit `LEGACY_SHIM` / `SHIM_REPLACEMENT` markers to:
  - `core_memory.memory_skill.form`
  - `core_memory.write_pipeline.window`
- Authored shim inventory artifact:
  - `docs/v2_p7c_shim_inventory.md`
- Added shim marker regression test:
  - `tests/test_p7c_shim_markers.py`

## Objective
Reduce remaining transitional seams by making shim/deprecated paths explicit and retiring safe ones without breaking mainline behavior.
