# PRD: Fix Mislabeled Circular-Import Workarounds

**Phase:** 2
**Status:** Not started
**Prerequisite:** Phase 0 complete (CI workflow merged) — Phase 1 not required

---

## Problem

Two `__init__.py` files contain code or docstrings that claim defensive measures
against circular imports — but no actual cycle exists. The defensive code is
either dead or misleading, and it makes the import graph harder for new
contributors to reason about.

### File 1: `core_memory/retrieval/__init__.py`

Contains a `__getattr__` lazy-import hook for `recall`:

```python
def __getattr__(name: str):
    if name == "recall":
        from .agent import recall
        return recall
    raise AttributeError(name)
```

Investigation of `core_memory/retrieval/agent.py` shows it imports from:
- `core_memory.retrieval.contracts`
- `core_memory.retrieval.tools.memory`

Neither of those imports `core_memory.retrieval.__init__` back. **No cycle exists.**
The lazy hook is dead defensive code from an earlier architecture.

### File 2: `core_memory/runtime/__init__.py`

Docstring claims:

```python
"""Runtime namespace package for canonical turn/flush execution modules.

Import submodules directly (e.g., core_memory.runtime.engine) to avoid
package-level circular imports.
"""
```

The empty `__init__.py` is a lazy-loading optimization (avoids eagerly loading
the heavy `engine.py`, `state.py`, etc. on `import core_memory.runtime`). It is
**not** a circular-import workaround. The docstring is misleading.

---

## Success criteria

1. `core_memory/retrieval/__init__.py` no longer has a `__getattr__` hook;
   `recall` is imported via a normal `from .agent import recall` statement.
2. `core_memory/runtime/__init__.py` docstring accurately describes the
   lazy-load rationale (no "circular import" claim).
3. `pytest tests/ -x -q` passes — if a real cycle existed, this would surface
   it immediately as an `ImportError`.
4. `python -c "from core_memory.retrieval import recall; print(recall)"` works.

---

## Sub-task 2a — Replace `__getattr__` with direct import in `retrieval/__init__.py`

Current:

```python
from .hybrid import hybrid_lookup
from .lexical import lexical_lookup
from .contracts import (
    EvidenceItem,
    RecallPlanning,
    RecallResult,
    RecallStep,
    SourceItem,
    recall_result_from_memory_execute,
    validate_recall_effort,
)


def __getattr__(name: str):
    if name == "recall":
        from .agent import recall

        return recall
    raise AttributeError(name)


__all__ = [
    "hybrid_lookup",
    "lexical_lookup",
    "recall",
    "EvidenceItem",
    "SourceItem",
    "RecallPlanning",
    "RecallResult",
    "RecallStep",
    "recall_result_from_memory_execute",
    "validate_recall_effort",
]
```

New:

```python
from .hybrid import hybrid_lookup
from .lexical import lexical_lookup
from .agent import recall
from .contracts import (
    EvidenceItem,
    RecallPlanning,
    RecallResult,
    RecallStep,
    SourceItem,
    recall_result_from_memory_execute,
    validate_recall_effort,
)


__all__ = [
    "hybrid_lookup",
    "lexical_lookup",
    "recall",
    "EvidenceItem",
    "SourceItem",
    "RecallPlanning",
    "RecallResult",
    "RecallStep",
    "recall_result_from_memory_execute",
    "validate_recall_effort",
]
```

**Why this is safe:**
- `retrieval/agent.py` does not import `retrieval` as a package; it imports
  specific submodules (`retrieval.contracts`, `retrieval.tools.memory`).
- If a hidden cycle existed, the test suite would `ImportError` on collection —
  the Phase 0 CI catches this immediately.

---

## Sub-task 2b — Rewrite `runtime/__init__.py` docstring

Current:

```python
"""Runtime namespace package for canonical turn/flush execution modules.

Import submodules directly (e.g., core_memory.runtime.engine) to avoid
package-level circular imports.
"""
```

New:

```python
"""Runtime namespace package for canonical turn/flush execution modules.

The package __init__ is intentionally empty: submodules (engine.py, state.py,
turn/, flush/, etc.) are heavy and rarely all needed at once, so callers
import submodules directly (e.g., `from core_memory.runtime.engine import ...`)
to keep import-time cost low.

This is a lazy-load optimization, not a circular-import workaround.
"""
```

**No code change.** Docstring only.

---

## Verification

```bash
# 1. The eager import works (would fail loudly if any hidden cycle exists)
pip install -e ".[dev]" -q
python -c "from core_memory.retrieval import recall; print(recall)"

# 2. Full test suite still passes
python -m pytest tests/ -x -q --tb=short

# 3. No __getattr__ left in retrieval/__init__.py
! grep -q '__getattr__' core_memory/retrieval/__init__.py

# 4. Docstring no longer claims circular-import workaround
! grep -q 'circular import' core_memory/runtime/__init__.py
```

---

## Guard rails

- **If the test suite fails on Sub-task 2a** with an `ImportError` about
  `core_memory.retrieval`, a real cycle was hiding. Investigate the failing
  import chain. Do not re-introduce `__getattr__` to mask it — fix the cycle
  by moving the offending import to function scope in the importing module.
- Sub-tasks 2a and 2b are independent. Land them in the same PR (small, related,
  diff is two files).
- Do not change `runtime/__init__.py` to add eager imports. The lazy pattern is
  intentional and load-time-sensitive.
