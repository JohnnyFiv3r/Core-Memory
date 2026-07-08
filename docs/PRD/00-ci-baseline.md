# PRD: CI + Coverage Baseline

**Phase:** 0
**Status:** Done
**Prerequisite:** None — this is the gating phase for all others

---

## Problem

There is no general-purpose CI workflow that runs `pytest tests/` on push or PR.
The existing workflows (`benchmark-locomo.yml`, `openclaw-bridge-smoke.yml`,
`publish-pypi.yml`) are all narrow-scope or manual. A bad import, a deleted file,
or a broken test can land on `main` undetected.

Phase 0 establishes the safety net before any code is touched.

---

## Success criteria

1. `.github/workflows/test.yml` exists and runs on every push and PR.
2. The `core-only` job installs `pip install -e ".[dev]"` and runs the
   core-deps pytest lane with optional backend/live tests deselected.
3. The `full` job installs `pip install -e ".[all,dev]"` and runs the suite
   with optional package-backed tests enabled and live Neo4j tests deselected.
4. Coverage is collected and uploaded as an artifact (no floor gate).
5. `pytest.mark.facade` is registered for graph compatibility/regression tests.
6. `pytest.mark.mixin_assembly` is registered for MemoryStore public assembly
   and persistence boundary wiring coverage.
7. `pytest.mark.pydanticai` is registered; both pydanticai test files gain a
   `skipif` guard so they skip cleanly in the `core-only` job.

---

## Implementation

### 1. `.github/workflows/test.yml`

Create with these three jobs:

```yaml
name: test

on:
  push:
    branches: ["**"]
    paths:
      - "core_memory/**"
      - "tests/**"
      - "pyproject.toml"
      - ".github/workflows/test.yml"
  pull_request:
    paths:
      - "core_memory/**"
      - "tests/**"
      - "pyproject.toml"
      - ".github/workflows/test.yml"

jobs:
  core-only:
    name: "pytest (core deps only)"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[dev]"
      - run: pytest tests/ -m "not optional_backend and not neo4j_live" -x -q --tb=short

  full:
    name: "pytest (all extras)"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[all,dev]"
      - run: pip install pytest-cov
      - run: pytest tests/ -m "not neo4j_live" -x -q --tb=short --cov=core_memory --cov-report=xml
      - uses: actions/upload-artifact@v4
        with:
          name: coverage-report
          path: coverage.xml

  py310:
    name: "pytest (Python 3.10)"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.10"
      - run: pip install -e ".[dev]"
      - run: pytest tests/ -m "not optional_backend and not neo4j_live" -x -q --tb=short
```

### 2. Register marks in `pyproject.toml`

Add to `[tool.pytest.ini_options]`:

```toml
markers = [
    "facade: graph compatibility/regression tests covering retained graph public surfaces",
    "mixin_assembly: tests that exercise MemoryStore public assembly and persistence boundary wiring",
    "optional_backend: tests that require an optional backend package or backend combination",
    "qdrant: tests that require the qdrant-client optional extra",
    "kuzu: tests that require the kuzu optional extra",
    "neo4j_pkg: tests that require the neo4j optional package but not a live server",
    "neo4j_live: tests that require a live Neo4j instance",
    "pydanticai: tests that require the pydanticai optional extra",
    "neo4j: tests that require a live Neo4j instance",
]
```

### 3. Apply `@pytest.mark.facade`

Add `import pytest` and `@pytest.mark.facade` to each test class or module-level
mark in these 11 files (all confirmed to import from `core_memory.graph.api`):

- `tests/test_graph_r2.py`
- `tests/test_graph_active_association_view_regressions.py`
- `tests/test_centrality_r3.py`
- `tests/test_r3_graph_semantic.py`
- `tests/test_structural_features_radius1.py`
- `tests/test_structural_inference_hardening.py`
- `tests/test_sync_structural_pipeline.py`
- `tests/test_backfill_causal_links.py`
- `tests/test_backfill_causal_links_targeted.py`
- `tests/test_association_confidence_compat.py`
- `tests/test_semantic_active_k.py`

Pattern for module-level mark (use this; avoids decorating every class):
```python
import pytest
pytestmark = pytest.mark.facade
```

### 4. Apply `@pytest.mark.mixin_assembly`

Same pattern for these 4 delegation/mixin files:

- `tests/test_store_core_delegates_mixin.py`
- `tests/test_store_add_bead_delegation.py`
- `tests/test_store_failure_ops_delegation.py`
- `tests/test_store_relationship_ops_delegation.py`

```python
import pytest
pytestmark = pytest.mark.mixin_assembly
```

### 5. Fix pydanticai tests

Both files hard-import `core_memory.integrations.pydanticai` at the top level,
which fails in the `core-only` job. Add a module-level skip guard:

```python
import importlib
import pytest

pytestmark = pytest.mark.pydanticai

pydantic_ai = importlib.util.find_spec("pydantic_ai")
if pydantic_ai is None:
    pytest.skip("pydantic-ai extra not installed", allow_module_level=True)
```

Files: `tests/test_pydanticai_adapter.py`, `tests/test_pydanticai_memory_tools.py`

---

## Verification

```bash
# Confirm marks are registered (no PytestUnknownMarkWarning)
pytest tests/ --collect-only -q 2>&1 | grep -i "unknown mark"

# Confirm facade tests are tagged
pytest tests/ -m facade --collect-only -q | head -20

# Confirm pydanticai tests skip without the extra
pip install -e ".[dev]" && pytest tests/test_pydanticai_adapter.py -v
# Expected: SKIPPED (pydantic-ai extra not installed)
```

---

## Guard rails

- Do not add a coverage floor gate in this phase. Establishing baseline first.
- Do not change any test logic — marks and skipif guards only.
- The `[all]` extra does not include `dev` (`pytest`, `ruff`); install both:
  `pip install -e ".[all,dev]"`.
- `pytest-cov` is not in any extras today; install it explicitly in the `full` job.
