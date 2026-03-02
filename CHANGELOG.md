# Changelog

## [1.0.0-core-migration-rc1] - 2026-03-02

### Added
- Core adapter routing from `mem-beads` to `core_memory` with compatibility translations.
- Direct core handlers for `link`, `recall`, `supersede`, `validate`, and `close --status promoted`.
- Core-native commands: `compact`, `uncompact`, `myelinate`.
- New `migrate-store` command for importing legacy `mem_beads` stores.
- Phase parity/hardening test coverage for deterministic behavior, fallback contracts, migration idempotency, and backup creation.

### Changed
- Canonical package flipped to `core-memory`.
- CLI entrypoints now target `core_memory.cli:main`.
- `mem-beads` retained as command alias for migration convenience.
- `setup.py` made non-interactive for pip build/editable workflows (`--onboard` preserved for manual onboarding).

### Fixed
- Editable install failures caused by interactive onboarding during build hooks.
- Packaging metadata generation path for setuptools editable builds.

### Validation
- `python3 test_phase1_parity.py`
- `PYTHONPATH=. python3 test_edges.py`
- `PYTHONPATH=. python3 test_e2e.py`
- `.venv/bin/python -m pip install -e .`
- `.venv/bin/core-memory --help`
- `.venv/bin/mem-beads --help`
