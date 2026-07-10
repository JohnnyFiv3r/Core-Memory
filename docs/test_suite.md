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

## Closeout Snapshot

Snapshot date: 2026-07-10, taken from `origin/master` after the cleanup
closeout truth sweep. Counts below are reproducible collection counts; execution
results depend on the installed optional packages and live-service availability.

| Lane | Collection / execution result | Meaning |
|---|---|---|
| Total suite collection | 2,286 tests | Broad local pytest collection with optional/live tests included |
| Core deps CI/local | 2,244 selected; 42 deselected by the marker expression | Core lane excludes optional backend and live Neo4j tests, so it has no expected environment-driven skips |
| All extras CI | 2,281 selected by `-m "not neo4j_live"` | Optional backend tests run when `[all,dev]` deps are installed; live Neo4j stays out of this lane |
| Broad local sweep | 2,286 collected | In a core-deps environment, the 42 optional/live tests may skip intentionally when their packages or service are unavailable |
| Optional backend bucket | 37 tests | Qdrant, Kuzu, Neo4j package, and combined retrieval backend coverage |
| Live Neo4j bucket | 5 tests | Requires `NEO4J_URI` and credentials; run only in the live backend lane |

If the core deps lane starts reporting skips instead of deselections, classify
the test with the appropriate optional/live marker or move it to an explicit
compatibility test bucket. If the all-extras lane reports optional backend
package skips, treat that as an environment/setup issue for the all-extras job,
not as stale test debt.

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

Store delegation cleanup has retired duplicated private-helper forwarding tests;
retained store coverage should exercise public behavior, compatibility ledger
surfaces, source/persistence side effects, or concrete regressions.

## Maintained Compatibility Coverage

Remaining compatibility tests are intentional public-surface coverage, not
cleanup debt:

- `pytest -m facade` protects retained graph compatibility facades and graph
  regression behavior while ledgered public imports remain supported.
- `pytest -m mixin_assembly` protects `MemoryStore` public assembly and
  persistence-boundary behavior after private forwarding tests were pruned.
- `tests/test_store_dream_bootstrap_ops_delegation.py` remains as focused
  coverage for the public store-oriented Dreamer legacy bridge until the
  compatibility ledger's removal condition is satisfied.
- Dedicated compatibility tests for runtime semantic-task facades, typed-search
  aliases, package-root memory-search exports, event-schema legacy imports, and
  persistence encryption are maintained by `docs/compatibility_ledger.md`.

## Health Guard

The test-suite health guard keeps skip and marker drift visible:

```bash
python scripts/check_test_suite_health.py --fail-on-violations
```

It enforces marker classification for optional backend and live Neo4j skips,
requires explicit reason text for skip/xfail calls, and rejects stale
`facade`/`mixin_assembly` marker descriptions that imply deletion status.
