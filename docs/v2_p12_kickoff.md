# V2-P12 Kickoff (Pre-OSS Stabilization)

Status: Active

## Objective
Stabilize the public architecture surface for OSS promotion without adding feature scope.

## Step plan (5)
1. Canonical docs lock ✅
2. Adapter consistency pass ✅
3. Public import/surface cleanup ✅
4. OSS trust test matrix ✅
5. Sweep + pre-OSS closeout ✅

## Step 5 completion notes
- Executed pre-OSS sweep via `scripts/run_pre_oss_matrix.sh`.
- Added closeout artifact:
  - `docs/v2_p12_pre_oss_closeout.md`
- Sweep result: 24 passed / 0 failed.
- V2P12 is now closed.

## Step 4 completion notes
- Added compact pre-OSS matrix runner:
  - `scripts/run_pre_oss_matrix.sh`
- Added matrix manifest test:
  - `tests/test_pre_oss_matrix.py`
- Matrix covers core, retrieval, and adapter invariants and is runnable in one command.
- Validation result: 24 passed / 0 failed.

## Step 1 completion notes
- Added short canonical doc set for fast contributor orientation:
  - `docs/architecture_overview.md`
  - `docs/write_side_flow.md`
  - `docs/retrieval_side_flow.md`
  - `docs/truth_hierarchy.md`
  - `docs/integration_contract.md`
- Updated docs index with explicit OSS canonical quick-start set.

## Step 2 completion notes
- Normalized adapter classification markers across launch adapter set:
  - OpenClaw bridge (`bridge`, `production_bridge`)
  - SpringAI bridge loader (`native`, `production_ready`)
  - PydanticAI runtime adapter (`native`, `production_ready`)
- Hardened PydanticAI adapter fail-open behavior around finalize emission.
- Added adapter-level metadata assertions and fail-open invariant tests in `tests/test_pydanticai_adapter.py`.

## Step 3 completion notes
- Added explicit public surface map for contributors:
  - `docs/public_surface.md`
- Updated docs index OSS quick-set to include public-surface map.
- Updated `README.md` top-level pointers to OSS quick-start docs and launch adapter classification.
- Updated `core_memory.__init__` module header to remove stale index-first wording and align with canonical event/session-first architecture.
