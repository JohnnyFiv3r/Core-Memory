# PR Summary — Core Memory Migration (`migrate-core-memory`)

## Title
Canonicalize `core_memory`, add safe legacy migration, and preserve CLI compatibility during transition.

## Why
This PR completes the planned migration from `mem_beads` to `core_memory` with safety and deterministic behavior as top priorities.

Goals addressed:
- make `core_memory` the canonical package/runtime
- preserve operator-facing CLI continuity (`mem-beads` alias)
- implement core-native compaction flows
- add explicit legacy import path (`migrate-store`)
- validate behavior with parity and hardening tests

## Decisions (resolved)
1. **Compaction path:** implemented core-native `compact` / `uncompact` / `myelinate`.
2. **Store strategy:** added explicit `migrate-store` command for legacy stores.
3. **Canonical flip:** switched package/entrypoint to `core_memory` now.

## What changed

### Packaging / entrypoints
- `pyproject.toml`
  - project name changed to `core-memory`
  - script entrypoints now target `core_memory.cli:main`
  - commands:
    - `core-memory` (canonical)
    - `mem-beads` (compat alias)

### Core features
- `core_memory/store.py`
  - added `compact(session_id=None, promote=False)`
  - added `uncompact(bead_id)`
  - added `myelinate(apply=False)` deterministic scaffold
  - added `migrate_legacy_store(legacy_root, backup=True)`

- `core_memory/cli.py`
  - added commands: `compact`, `uncompact`, `myelinate`, `migrate-store`

### Adapter compatibility
- `core_memory/adapter_cli.py`
  - expanded command support + legacy arg translation
  - direct handlers for `link`, `recall`, `supersede`, `validate`, `close --status promoted`
  - controlled fallback for unsupported paths

- `mem_beads/cli.py`, `mem_beads/__main__.py`
  - routing through adapter when opted in
  - legacy fallback retained where needed

### Build/install reliability
- `setup.py`
  - removed interactive onboarding from default build path
  - keeps onboarding available via explicit `--onboard`

### Documentation
- `README.md` updated for canonical `core-memory` usage and migration examples
- `CHANGELOG.md` added with migration RC notes
- `COMPATIBILITY_SPEC.md` and `MIGRATION_PLAN.md` updated to reflect finalized decisions/status

## Validation performed
- `python3 test_phase1_parity.py`
- `PYTHONPATH=. python3 test_edges.py`
- `PYTHONPATH=. python3 test_e2e.py`
- `python3 -m venv .venv`
- `.venv/bin/python -m pip install -e .`
- `.venv/bin/core-memory --help`
- `.venv/bin/mem-beads --help`

All passed in current branch state.

## Safety / migration behavior
- `migrate-store` includes default backup creation for existing core index.
- migration tests include:
  - idempotency (second run no duplicate bead imports)
  - backup presence
  - clean failure for missing legacy path

## Known limits (explicit)
- some legacy command semantics are still compatibility-routed/fallback-based where full core parity is not yet implemented.
- myelination is currently deterministic scaffold behavior pending policy expansion.

## Rollback plan
If needed, rollback by:
1. reverting to pre-flip commit on branch,
2. restoring entrypoint mapping to legacy target,
3. preserving migrated store backups (`index.backup.*.json`) for restore.

## Suggested merge checklist
- [ ] final maintainer pass on README examples
- [ ] final pass on compatibility matrix wording
- [ ] squash strategy confirmed (or keep granular migration commits)
- [ ] merge `migrate-core-memory` into `master`
