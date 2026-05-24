# PRD: Remove `graph/api.py` Compat Facade

**Phase:** 4
**Status:** Not started
**Prerequisite:** Phase 0 CI passing

---

## Problem

The `graph/` module was refactored into four split modules (`_api_impl.py`, `structural.py`,
`traversal.py`, `semantic.py`) but the old entry point `graph/api.py` was left in place as
a compatibility facade. It now exports 21 symbols — 4 locally-defined wrappers and 17
re-exports — none of which belong there.

This creates two concrete problems:

1. **Parameter transformation bug hidden in the facade.** `graph/api.py:causal_traverse`
   converts `anchor_ids` from positional to keyword-only before forwarding to
   `causal_traverse_chains`. The CLI caller at `cli_handlers_graph.py:43` passes it
   positionally. Removing the facade without fixing this first breaks the `graph trace`
   command.

2. **False import surface.** `graph/__init__.py` star-imports `api.py`, so
   `from core_memory.graph import build_graph` silently resolves through two indirection
   layers. External consumers get a stable-looking path that is actually a double shim.

---

## Current state

| Component | Status |
|-----------|--------|
| `graph/api.py` | Compat facade — 21 exported symbols, 0 real logic |
| `graph/_api_impl.py` | Real implementation — `build_graph`, `graph_stats` |
| `graph/structural.py` | Real implementation — structural edge ops |
| `graph/traversal.py` | Real implementation — `causal_traverse_chains` and helpers |
| `graph/semantic.py` | Real implementation — semantic edge ops |
| `graph/__init__.py` | Star-imports `api.py`, re-exports to bare `core_memory.graph` |
| `cli_handlers_graph.py:7-16` | Imports 8 functions from `core_memory.graph.api` |
| Test files importing `graph.api` | 12 files across `tests/` |

---

## Success criteria

1. `core_memory/graph/api.py` does not exist.
2. `core_memory/graph/__init__.py` re-exports the same public symbols directly from the
   split modules (no change to `from core_memory.graph import X` for external callers).
3. `cli_handlers_graph.py` imports from split modules; all 8 graph subcommands work.
4. All 12 test files that previously imported from `graph.api` import from split modules.
5. Full pytest suite passes. `pytest -m facade` (tagged in Phase 0) passes.
6. `_api_impl.py` either renamed to remove the private prefix or its functions folded
   into the split modules — no `_`-prefixed implementation files on the public graph path.

---

## Scope

**In:**
- `graph/api.py` deletion
- `graph/__init__.py` re-export cleanup
- `cli_handlers_graph.py` import migration
- 12 test file import migrations
- `causal_traverse` signature fix in `graph/traversal.py`
- Optional: rename `_api_impl.py`

**Out:**
- Any change to what the graph functions actually do
- Changes to `structural.py`, `traversal.py`, `semantic.py` beyond the signature fix
- New graph capabilities

---

## Implementation order

Each step ends with a full `pytest` run before proceeding.

**Step 1 — Fix the signature mismatch**

In `core_memory/graph/traversal.py`, make `anchor_ids` accept both positional and keyword
by changing the signature of `causal_traverse_chains` from:

```python
def causal_traverse_chains(root, *, anchor_ids, ...):
```

to:

```python
def causal_traverse_chains(root, anchor_ids=None, *, ...):
```

or add a positional overload. Verify `cli_handlers_graph.py:43` call still works.

**Step 2 — Migrate `cli_handlers_graph.py`**

Replace the 8 imports from `core_memory.graph.api` with direct imports from the
appropriate split module. Cross-reference each symbol:

| Symbol | Lives in |
|--------|----------|
| `build_graph` | `graph._api_impl` |
| `graph_stats` | `graph._api_impl` |
| `causal_traverse` | `graph.traversal` (via `causal_traverse_chains`) |
| `add_structural_edge` | `graph.structural` |
| `backfill_causal_links` | `graph.structural` |
| `sync_structural_pipeline` | `graph.structural` |
| `add_semantic_edge` | `graph.semantic` |
| `decay_semantic_edges` | `graph.semantic` |

**Step 3 — Migrate test files**

Update each of the 12 test files. Same symbol→module mapping as Step 2. No logic changes —
import line replacement only.

Files:
- `tests/test_semantic_active_k.py`
- `tests/test_backfill_causal_links_targeted.py`
- `tests/test_backfill_causal_links.py`
- `tests/test_graph_r2.py`
- `tests/test_sync_structural_pipeline.py`
- `tests/test_centrality_r3.py`
- `tests/test_association_confidence_compat.py`
- `tests/test_graph_active_association_view_regressions.py`
- `tests/test_r3_graph_semantic.py`
- `tests/test_structural_inference_hardening.py`
- `tests/test_structural_features_radius1.py`
- Any additional files found by `grep -r "graph.api" tests/`

**Step 4 — Update `graph/__init__.py`**

Replace:
```python
from .api import *
```

With explicit re-exports directly from split modules. Enumerate every symbol that was
re-exported through `api.py` and wire it to its real home.

**Step 5 — Delete `graph/api.py`**

Run `pytest -m facade` + full suite.

**Step 6 (optional) — Rename `_api_impl.py`**

Consider `graph/core.py` or folding `build_graph`/`graph_stats` into `structural.py`
since they compose structural operations. Decision deferred to implementation time.
