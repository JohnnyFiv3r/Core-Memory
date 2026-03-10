# V2-P21 Kickoff (Post-Review Integrity + Packaging Fixes)

Status: Active

## Objective
Address remaining post-review correctness and packaging issues (store integrity, ranking correctness, package-data completeness, platform support clarity).

## Step plan (1)
1. Integrity + packaging/docs fix pass ✅

## Completion notes
- `MemoryStore.add_bead(...)` now rejects reserved-field overrides to prevent id mismatch integrity bugs.
- `find_failure_signature_matches(...)` ranking fixed to preserve intended ordering (overlap desc, recency desc).
- Packaging metadata updated to include:
  - `core_memory/py.typed`
  - `core_memory/data/*.json`
- Platform support documented and classifiers updated (Linux/macOS supported; Windows currently unsupported due to POSIX locking layer).
- Added regression tests:
  - `tests/test_v2p21_store_integrity.py`
