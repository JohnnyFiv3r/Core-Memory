# PRD: Harden the PydanticAI Adapter Boundary

**Phase:** 3A
**Status:** Complete — optional-adapter import boundary guard shipped
**Prerequisite:** Phase 0 complete

---

## Current implementation note

Phase 3A is complete in the current tree. The public cleanup status and PRD
index both mark it done, and `tests/test_adapter_boundary_pydanticai.py` now
enforces the intended boundary with subprocess-isolated import checks.

The shipped test proves that importing `core_memory` and key internal
subpackages does not load `pydantic_ai` into `sys.modules`, while an explicit
import of `core_memory.integrations.pydanticai` remains allowed when the
optional package is installed. The cleanup plan also records all three Phase 3A
tasks as complete.

The plan below is retained as historical rationale for the boundary and for the
shape of the enforcement test. The test file is the authoritative source for
the exact current module list.

---

## Historical problem

`pydantic-ai` is an optional adapter, declared in `[project.optional-dependencies]`
as `pydanticai = ["pydantic-ai"]`. The intent is that `import core_memory` works
in a fresh environment with only `pyyaml` installed — no `pydantic-ai`, no
`fastapi`, no `faiss`.

Before Phase 3A, there was no mechanism that enforced this. A future contributor could:
- Add `from core_memory.integrations.pydanticai import ...` at the top of a
  core module (`runtime/`, `retrieval/`, `persistence/`).
- Make `pydantic_ai` a transitive side-effect of `import core_memory`.

Either would have silently turned `pydantic-ai` into a hard dependency. CI might
not have noticed because Phase 0's `core-only` job did not select the optional
pydanticai tests.

---

## Completed work

- Added `pytest.mark.pydanticai` registration to `pyproject.toml`
- Added module-level `skipif(find_spec("pydantic_ai") is None, ...)` guards to
  both `tests/test_pydanticai_adapter.py` and `tests/test_pydanticai_memory_tools.py`
- Added `core-only` job to `.github/workflows/test.yml` that installs only
  `[dev]` extras (no pydanticai)
- Added `tests/test_adapter_boundary_pydanticai.py`, which proves that
  `import core_memory` and key internal-package imports do not side-load
  `pydantic_ai` into `sys.modules`

---

## Success criteria / outcome

1. `tests/test_adapter_boundary_pydanticai.py` runs as part of the
   default suite and asserts `pydantic_ai not in sys.modules` after a fresh
   `import core_memory`.
2. The same test asserts the same for `import core_memory.runtime`,
   `import core_memory.retrieval`, and `import core_memory.persistence`.
3. The test passes in the `core-only` CI job (where `pydantic_ai` is not
   installed).
4. The test passes in the `full` CI job (where `pydantic_ai` IS installed) —
   importing the integration submodule explicitly is still allowed.

---

## Historical implementation

### Sub-task 3A.1 — Add the boundary enforcement test

The shipped test lives at `tests/test_adapter_boundary_pydanticai.py`. The
original sketch below is retained for design intent only; keep the checked-in
test file authoritative for the current module list and marker details.

```python
"""Enforce that pydantic_ai is never loaded as a side effect of importing
core_memory or any internal subpackage.

If this test fails, a core module added an unguarded `import pydantic_ai`
or a transitive import path was introduced. Fix by:
  1. Moving the import to function scope inside an integration module, OR
  2. Wrapping with `importlib.util.find_spec("pydantic_ai")` guard.
"""

from __future__ import annotations

import subprocess
import sys


CORE_MODULES = (
    "core_memory",
    "core_memory.runtime",
    "core_memory.runtime.engine",
    "core_memory.retrieval",
    "core_memory.persistence",
    "core_memory.persistence.store",
    "core_memory.schema",
    "core_memory.graph",
    "core_memory.claim",
    "core_memory.entity",
    "core_memory.association",
)


def _import_in_subprocess(module: str) -> set[str]:
    """Import `module` in a fresh Python process; return sys.modules keys."""
    code = (
        f"import {module}; "
        "import sys, json; "
        "print(json.dumps(sorted(sys.modules.keys())))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    import json
    return set(json.loads(result.stdout.strip().splitlines()[-1]))


def test_import_core_memory_does_not_load_pydantic_ai():
    """`import core_memory` must not side-load pydantic_ai."""
    loaded = _import_in_subprocess("core_memory")
    leaked = {m for m in loaded if m == "pydantic_ai" or m.startswith("pydantic_ai.")}
    assert not leaked, (
        f"pydantic_ai leaked into sys.modules via `import core_memory`: {leaked}. "
        "Likely cause: a core module added an unguarded `import pydantic_ai` or "
        "an unguarded `from core_memory.integrations.pydanticai import ...`."
    )


def test_internal_subpackages_do_not_load_pydantic_ai():
    """No internal subpackage import should side-load pydantic_ai."""
    for module in CORE_MODULES:
        loaded = _import_in_subprocess(module)
        leaked = {m for m in loaded if m == "pydantic_ai" or m.startswith("pydantic_ai.")}
        assert not leaked, (
            f"pydantic_ai leaked into sys.modules via `import {module}`: {leaked}"
        )


def test_explicit_pydanticai_integration_import_is_allowed():
    """The integration module itself is allowed to import pydantic_ai —
    that's its whole purpose. This test exists to confirm we're not over-
    restricting; if pydantic_ai is installed, the explicit import works."""
    import importlib.util
    if importlib.util.find_spec("pydantic_ai") is None:
        import pytest
        pytest.skip("pydantic-ai not installed; explicit import test skipped")

    loaded = _import_in_subprocess("core_memory.integrations.pydanticai")
    # When the user explicitly imports the integration, pydantic_ai MUST load.
    assert any(
        m == "pydantic_ai" or m.startswith("pydantic_ai.") for m in loaded
    ), "Explicit pydanticai integration import did not load pydantic_ai"
```

**Why subprocess isolation:**
Once `pydantic_ai` has been loaded into `sys.modules` by an earlier test in the
same Python session, subsequent in-process checks lose signal. The subprocess
spawn guarantees a clean module table per assertion.

---

### Sub-task 3A.2 — Update `docs/cleanup-plan.md`

The Phase 3A bullet list in `docs/cleanup-plan.md` is now complete:

```markdown
## Phase 3A — Harden the PydanticAI Boundary

**Goal:** Lock in that `pydantic-ai` is optional; prevent it silently becoming required.

- [x] Add `pytest.mark.skipif` guards to pydanticai tests *(done in Phase 0)*
- [x] Add CI matrix entry that installs without `[pydanticai]` *(done in Phase 0)*
- [x] Add `tests/test_adapter_boundary_pydanticai.py` — subprocess-isolated
      assertion that `pydantic_ai` does NOT appear in `sys.modules` after
      `import core_memory` (and key subpackages)

**PRD:** `docs/PRD/03a-pydanticai-boundary.md`
```

---

## Verification

```bash
# 1. Test runs and passes without pydantic-ai installed
pip install -e ".[dev]" -q
python -m pytest tests/test_adapter_boundary_pydanticai.py -v

# Expected output:
#   test_import_core_memory_does_not_load_pydantic_ai PASSED
#   test_internal_subpackages_do_not_load_pydantic_ai PASSED
#   test_explicit_pydanticai_integration_import_is_allowed SKIPPED

# 2. Test runs and passes WITH pydantic-ai installed
pip install -e ".[dev,pydanticai]" -q
python -m pytest tests/test_adapter_boundary_pydanticai.py -v

# Expected output:
#   test_import_core_memory_does_not_load_pydantic_ai PASSED
#   test_internal_subpackages_do_not_load_pydantic_ai PASSED
#   test_explicit_pydanticai_integration_import_is_allowed PASSED

# 3. Sanity: deliberately break it to confirm the test detects leakage.
#    (Optional manual verification — do not commit.)
#    Add `import pydantic_ai` to core_memory/runtime/__init__.py temporarily,
#    re-run the test. It MUST fail with the "leaked into sys.modules" message.
#    Revert the change.
```

---

## Guard rails

- **Do not** weaken the test to allow `pydantic_ai` in sys.modules. The whole
  point is that core never imports it.
- **Do not** use in-process `sys.modules` inspection without subprocess
  isolation. Other tests may have already imported pydanticai earlier in the
  session, polluting the check.
- If this test starts failing, the fix is **never** to update the allowed set.
  The fix is always to:
  1. Find the new core-side import of `pydantic_ai`.
  2. Move it to function scope inside the integration, or
  3. Guard it with `importlib.util.find_spec("pydantic_ai") is not None`.
- The same pattern (subprocess + sys.modules assertion) can be cloned for other
  optional adapters later (`fastapi`, `faiss`, `neo4j`, etc.). Not in scope for
  Phase 3A — file a separate issue if you want that.
