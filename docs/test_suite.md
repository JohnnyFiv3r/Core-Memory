# Test Suite

Status: Canonical testing guide

Use this page for the current pytest lanes. Historical PRDs may show older
commands from before optional backend tests were separated from the core CI
lane.

## Core Commands

| Purpose | Setup | Command |
|---|---|---|
| Core deps CI/local | `pip install -e ".[dev]"` | `pytest tests/ -m "not optional_backend and not neo4j_live" -x -q --tb=short` |
| All extras CI | `pip install -e ".[all,dev]" && pip install pytest-cov` | `pytest tests/ -m "not neo4j_live" -x -q --tb=short --cov=core_memory --cov-report=xml` |
| Broad local sweep | any suitable dev env | `pytest tests/` |
| Optional backend collection | `pip install -e ".[all,dev]"` | `pytest tests/ -m optional_backend --collect-only -q` |
| Live Neo4j | `pip install -e ".[dev,neo4j]"` plus `NEO4J_URI`/credentials | `pytest tests/test_neo4j_live.py -v --tb=short -m neo4j_live` |

The broad local sweep intentionally keeps normal pytest behavior: optional
backend tests are still collected and will skip when their packages or live
services are not available. CI uses marker expressions to keep the core lane
skip-free while preserving optional backend coverage in the all-extras lane.

## Marker Semantics

- `optional_backend`: package-backed backend tests that should not run in the
  core-deps lane.
- `qdrant`, `kuzu`, `neo4j_pkg`: backend package families under
  `optional_backend`.
- `neo4j_live`: live Neo4j integration tests that require a running server.
- `facade`: graph compatibility/regression coverage for retained graph public
  surfaces.
- `mixin_assembly`: MemoryStore public assembly and persistence boundary
  wiring coverage.
- `pydanticai`, `graphiti`, `obsidian`: optional integration families.

Do not use `facade` or `mixin_assembly` as deletion markers. Compatibility
surfaces are governed by `docs/compatibility_ledger.md`; test pruning should
remove only duplicated implementation-lock tests after equivalent public
behavior coverage exists.
