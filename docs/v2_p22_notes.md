# V2-P22 Notes (OSS Readiness Cleanup)

Status: Complete

## Changes
- Removed hardcoded private workspace paths from tests:
  - `tests/test_v2_p2_enforcement_matrix.py`
  - `tests/test_query_anchor_resolution.py`
  - `tests/test_write_trigger_dispatch.py`
- Added platform-guarded lock behavior in `core_memory/io_utils.py` with explicit runtime error for non-POSIX locking environments.
- Clarified README:
  - OpenClaw integration is optional
  - optional extras installation (`[http]`, `[semantic]`)
  - added end-to-end quickstart example (turn -> flush -> retrieval)
- Added semantic extras in `pyproject.toml`:
  - `numpy`, `faiss-cpu`

## Validation
- `tests.test_v2_p2_enforcement_matrix`
- `tests.test_query_anchor_resolution`
- `tests.test_write_trigger_dispatch`
- `tests.test_pre_oss_matrix`
(all passing)
