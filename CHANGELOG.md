# Changelog

## [Unreleased]

### Changed
- Removed the private root duplicate
  `core_memory/runtime/session_enrichment_delta.py`. The live session enrichment
  delta normalizer remains
  `core_memory/runtime/session/session_enrichment_delta.py`, with no behavior
  change.
- Removed the private root duplicate `core_memory/runtime/goal_lifecycle.py`.
  The live goal lifecycle pass remains
  `core_memory/runtime/session/goal_lifecycle.py`, with no runtime behavior
  change.
- Removed the private root duplicate `core_memory/cli_handlers_semantic.py`.
  The live semantic CLI handler remains `core_memory/cli/handlers/semantic.py`,
  with no CLI command behavior change.
- Moved the governed management facade from root flat file
  `core_memory/management.py` to package module `core_memory/management/`.
  The public import path `core_memory.management` and package-root exports are
  unchanged.
- Removed the private `core_memory.retrieval.trace` compatibility wrapper.
  Low-level trace callers should use `core_memory.memory_trace` or
  `core_memory.retrieval.tools.memory.trace`; internal callers use
  `core_memory.retrieval.pipeline.canonical.trace_request`.
- Removed the private `core_memory.persistence.write_ops` compatibility shim
  after import scans found no active callers. Write behavior remains on
  `MemoryStore` and canonical runtime boundaries such as
  `process_turn_finalized`.
- Removed the private `core_memory.retrieval.pipeline.explain.build_explain`
  compatibility shim. The live explain payload remains inline in
  `core_memory.retrieval.pipeline.memory_search_request(..., explain=True)`.
- **Phase 9h (complete):** Deleted all 52 backward-compat shims created during Phase 9
  structural moves and migrated every callsite to canonical paths:
  - 31 `runtime/` root shims (9e dreamer + 9f subpackage) → `runtime/{turn,flush,session,passes,queue,observability,dreamer}/`
  - 7 `integrations/openclaw_*.py` shims (9c) → `integrations/openclaw/`
  - 14 `core_memory/cli_*.py` shims (9d) → `core_memory/cli/`
  - 90+ import callsites in tests, eval, benchmarks, and source files updated.
  - `scripts/openclaw_bridge_ci_smoke.sh` updated to canonical module path.
  - `runtime/` root now contains only `engine.py`, `state.py`, `event_schemas.py`, `__init__.py`.
- Updated `docs/contributor_map.md` to reference canonical subpackage paths.
- **Phase 9g:** Fixed layering violation — `retrieval/pipeline/canonical.py` top-level
  import of `hydrate_bead_sources` from `integrations/api` converted to lazy function-level
  imports, breaking the `retrieval → integrations → runtime → retrieval` cycle risk.
  `hydrate_bead_sources` added to `core_memory.__init__` public API.
  `retrieval/__init__.py` `__getattr__` guard documented accurately.

## [1.1.0] - 2026-05-09

### Added
- Deterministic OpenClaw bridge operations scripts:
  - `scripts/openclaw_bridge_install.sh`
  - `scripts/openclaw_bridge_doctor.sh`
  - `scripts/openclaw_bridge_ci_smoke.sh`
- CI smoke workflow: `.github/workflows/openclaw-bridge-smoke.yml`

### Changed
- OpenClaw bridge plugin manifest schema now includes `coreMemoryRepo`.
- OpenClaw integration docs now document canonical install/verify path and runtime verification signals.
- Association inference v2.1 hardening: crawler/model-inferred association ingestion now validates a strict canonical inference subset by default and quarantines malformed/non-canonical rows (instead of silent upgrades).
- Causal grounding policy now requires at least one non-temporal structural relation for full grounding; `follows`/`associated_with`-only chains downgrade to partial grounding.
- Canonical retrieval cleanup: removed deprecated public retrieval surfaces (`/v1/memory/search-form`, `/v1/memory/reason`, OpenClaw bridge `search-form`/`reason`) and aligned adapters/docs to `search`/`trace`/`execute`.
- Canonical planner authority cleanup: removed legacy `retrieval/pipeline/execute.py`; canonical planner authority is `retrieval/pipeline/canonical.py`.
- Test contract cleanup: retired obsolete search-form/reason legacy test modules and rebaselined active retrieval contract coverage around canonical search/trace/execute + hydration/tenant/v2.1 policies.

### Fixed
- Bridge ingestion modules now read stdin fully to avoid truncated JSON payload parsing failures in large event envelopes.

## [1.0.1] - 2026-03-03

### Fixed
- Removed broken legacy association runner scripts that still depended on removed `mem_beads` runtime path (`associate.py`, `run_association.py`, `run_association.sh`).
- Removed archived `core_memory/adapter_cli.py` compatibility scaffold to eliminate command-path ambiguity.
- Cleaned CLI module text to reflect canonical `core-memory` command.

### Changed
- Finalized compatibility/deprecation docs for post-migration reality:
  - `core-memory` is canonical and sole CLI
  - `mem-beads` alias is removed
  - legacy store import remains via `migrate-store`

### Note
- Historical RC sections below (1.0.0-rc1/rc2) intentionally mention `mem-beads` alias validation because that alias existed during migration testing; alias removal is final as of 1.0.1.

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

## [1.0.0-core-migration-rc2] - 2026-03-02

### Added
- `DEPRECATION_PLAN.md` with N / N+1 / N+2 removal schedule.
- `scripts/migration_drill.sh` for repeatable migration smoke tests.

### Changed
- Automation paths repointed from `tools/mem-beads/*` to canonical root scripts.
- `extract-beads.py` now writes via `core-memory` CLI (`add`) instead of legacy script path.
- `mem_beads` shim is now core-only (legacy fallback removed).
- `mem-beads` command now emits deprecation warning.

### Fixed
- `migrate-store` association idempotency (no duplicate association imports on rerun).

### Validation
- `./scripts/migration_drill.sh /tmp/core-memory-drill-run2`
- `python3 test_phase1_parity.py`
- `PYTHONPATH=. python3 test_edges.py`
- `PYTHONPATH=. python3 test_e2e.py`
