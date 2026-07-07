# PRD: Classify `graph/api.py` Compat Facade

**Phase:** 4
**Status:** Superseded by `docs/compatibility_ledger.md`; classified-retained
**Prerequisite:** Phase 0 CI passing

---

## Current decision

The original deletion plan is superseded. `core_memory/graph/api.py` is now a
public compatibility facade, not a dead artifact. Do not delete it as cleanup
work unless the compatibility ledger's deprecation/removal condition has been
satisfied.

Completed cleanup work moved internal callers and package-level re-exports to
the split graph modules while keeping `core_memory.graph.api.*` available for
legacy external imports.

## Problem

The `graph/` module was originally refactored into split modules (`core.py`,
`structural.py`, `traversal.py`, `semantic.py`) while the old entry point
`graph/api.py` remained as a compatibility facade.

This creates two concrete problems:

1. **Parameter transformation bug hidden in the facade.** `graph/api.py:causal_traverse`
   converts `anchor_ids` from positional to keyword-only before forwarding to
   `causal_traverse_chains`. The CLI caller at `cli/handlers/graph.py:43` passes it
   positionally. Removing the facade without fixing this first breaks the `graph trace`
   command.

2. **False import surface.** `graph/__init__.py` originally star-imported
   `api.py`, so `from core_memory.graph import build_graph` silently resolved
   through two indirection layers. That has since been resolved: package-level
   `core_memory.graph` exports now point directly at split modules, while
   `core_memory.graph.api` remains as a direct legacy import path.

---

## Current state

| Component | Status |
|-----------|--------|
| `graph/api.py` | Public compatibility facade for legacy imports |
| `graph/core.py` | Real implementation — `build_graph`, `graph_stats` |
| `graph/structural.py` | Real implementation — structural edge ops |
| `graph/traversal.py` | Real implementation — `causal_traverse_chains` and helpers |
| `graph/semantic.py` | Real implementation — semantic edge ops |
| `graph/__init__.py` | Explicit re-exports from split modules |
| `cli/handlers/graph.py` | Imports from split modules |
| Test files importing `graph.api` | Facade tests only |

---

## Success criteria

1. `core_memory/graph/api.py` is retained and documented as a public
   compatibility facade.
2. `core_memory/graph/__init__.py` re-exports the same public symbols directly from the
   split modules (no change to `from core_memory.graph import X` for external callers).
3. `cli/handlers/graph.py` imports from split modules; all 8 graph subcommands work.
4. Active first-party code and tests no longer import from `graph.api` except
   facade/compatibility tests and historical docs/ledger references.
5. Full pytest suite passes. `pytest -m facade` (tagged in Phase 0) passes.
6. `core.py` owns the former `_api_impl.py` implementation surface.

---

## Scope

**In:**
- `graph/api.py` public/private classification
- `graph/__init__.py` re-export cleanup
- `cli/handlers/graph.py` import migration
- 12 test file import migrations
- `causal_traverse` signature fix in `graph/traversal.py`
- `_api_impl.py` rename to `core.py`

**Out:**
- Any change to what the graph functions actually do
- Changes to `structural.py`, `traversal.py`, `semantic.py` beyond the signature fix
- New graph capabilities

---

## Completed implementation summary

**Step 1 — Signature mismatch resolved**

The CLI now imports `causal_traverse_chains` from `core_memory.graph.traversal`
as `causal_traverse`, so it no longer depends on the facade's old positional to
keyword forwarding behavior.

**Step 2 — `cli/handlers/graph.py` migrated**

The graph CLI imports directly from the appropriate split modules:

| Symbol | Lives in |
|--------|----------|
| `build_graph` | `graph.core` |
| `graph_stats` | `graph.core` |
| `causal_traverse` | `graph.traversal` (via `causal_traverse_chains`) |
| `add_structural_edge` | `graph.structural` |
| `backfill_causal_links` | `graph.structural` |
| `sync_structural_pipeline` | `graph.structural` |
| `add_semantic_edge` | `graph.semantic` |
| `decay_semantic_edges` | `graph.semantic` |

**Step 3 — Test imports migrated**

Active tests import split modules or package-level `core_memory.graph`
re-exports. Facade-specific tests remain only where they prove compatibility.

**Step 4 — `graph/__init__.py` updated**

`core_memory.graph` now explicitly re-exports symbols directly from split
modules instead of star-importing `api.py`.

**Step 5 — Classify and retain `graph/api.py`**

Record `graph/api.py` in `docs/compatibility_ledger.md` as a public
compatibility facade. Keep it until the ledger's deprecation/removal condition
has passed. Run `pytest -m facade` + full suite.

**Step 6 — `_api_impl.py` renamed**

The former `_api_impl.py` implementation surface now lives in
`core_memory/graph/core.py`.
