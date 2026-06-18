"""Enforce that pydantic_ai is never loaded as a side effect of importing
core_memory or any internal subpackage.

If this test fails, a core module added an unguarded `import pydantic_ai`
or a transitive import path was introduced. Fix by:
  1. Moving the import to function scope inside an integration module, OR
  2. Wrapping with `importlib.util.find_spec("pydantic_ai")` guard.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys

import pytest

pytestmark = pytest.mark.pydanticai


CORE_MODULES = (
    "core_memory",
    "core_memory.runtime",
    "core_memory.runtime.semantic_tasks",
    "core_memory.runtime.semantic_tasks.contracts",
    "core_memory.runtime.semantic_tasks.runtime",
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
    that's its whole purpose. This test confirms we're not over-restricting;
    if pydantic_ai is installed, the explicit import works."""
    if importlib.util.find_spec("pydantic_ai") is None:
        pytest.skip("pydantic-ai not installed; explicit import test skipped")

    loaded = _import_in_subprocess("core_memory.integrations.pydanticai")
    assert any(
        m == "pydantic_ai" or m.startswith("pydantic_ai.") for m in loaded
    ), "Explicit pydanticai integration import did not load pydantic_ai"
