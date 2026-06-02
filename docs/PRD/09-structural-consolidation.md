# PRD: Structural Consolidation — runtime, CLI, OpenClaw, Dreamer

**Phase:** 9
**Status:** Complete (9a–9h)
**Prerequisite:** Phases 4 and 5 complete (graph and persistence cleaned up)

> Phase 9 is complete: all 52 backward-compat shims deleted, callsites
> migrated to canonical subpackage paths, layering violation in
> `retrieval/pipeline/canonical.py` fixed via lazy import. The discussion
> below is preserved as historical design context — paths referenced as
> shim destinations now exist only at the canonical locations described.

---

## Problem

Three structural problems make the codebase feel accreted rather than designed:

1. **`runtime/` is a flat pile of 33 files** at six different abstraction levels (flows,
   passes, queue infrastructure, concepts, state holders, observability) with no internal
   organization. Every concept that touched the orchestrator over time got a new top-level
   file.

2. **OpenClaw is silently privileged.** Every other integration has its own subdirectory
   (`mcp/`, `neo4j/`, `pydanticai/`, etc.). OpenClaw has 7 flat files at the
   `integrations/` root — because it was the first integration built and the folder
   pattern hadn't crystallized yet. Worse, OpenClaw's namespace is embedded in Core
   Memory's event schema strings (`"openclaw.memory.flush_report.v1"` in `engine.py` and
   `flush_flow.py`). Most of `openclaw_flags.py` contains generic Core Memory feature
   flags that have nothing to do with OpenClaw.

3. **The CLI is a flat dump of 13 files** at `core_memory/`'s top level. `dreamer.py`
   is split across the top level (execution logic) and `runtime/` (candidate management,
   evaluation) with no coherent home.

Each of these is a cosmetic-but-meaningful inconsistency: when someone new reads the
codebase, they cannot predict what the directory layout means.

---

## Success criteria

1. `runtime/` has subdirectories for turn, flush, session, passes, queue, dreamer, and
   observability. `engine.py` stays at the root as the visible orchestrator entry point.
2. All OpenClaw integration files live in `integrations/openclaw/`. Generic feature flags
   have been moved to `core_memory/config/feature_flags.py`.
3. Schema strings in `engine.py` and `flush_flow.py` no longer embed `"openclaw."` in
   the namespace.
4. The CLI lives in `core_memory/cli/` with `parsers/` and `handlers/` subdirectories.
   The entry point `core_memory.cli:main` continues to work without `pyproject.toml`
   changes.
5. `dreamer.py` execution logic lives in `runtime/dreamer/` alongside candidate
   management and evaluation.
6. `integrations/api.py` depends only on `core_memory`'s public API, not on internal
   module paths.
7. Full pytest suite passes. No import errors on `import core_memory`.

---

## Sub-task 9a — Extract generic feature flags from `openclaw_flags.py`

**Do this first.** Everything else in Phase 9 that touches OpenClaw imports depends on
this step being complete.

`openclaw_flags.py` contains a mix of generic Core Memory flags and one OpenClaw-specific
flag. Separate them:

**Move to `core_memory/config/feature_flags.py` (new file):**
- `core_memory_enabled()`
- `transcript_archive_enabled()`
- `transcript_hydration_enabled()`
- `soul_promotion_enabled()`
- `default_hydrate_tools_enabled()`
- `default_adjacent_turns()`

**Keep in OpenClaw integration (Step 9b):**
- `supersede_openclaw_summary_enabled()`

Update every importer of `core_memory.integrations.openclaw_flags` to import from
`core_memory.config.feature_flags` instead. The primary importers are:
- `core_memory/runtime/engine.py`
- `core_memory/runtime/flush_flow.py`
- `core_memory/integrations/api.py`
- `core_memory/integrations/http/server.py`
- Any additional files found by `grep -r "openclaw_flags" core_memory/`

After this step, nothing in `core_memory/runtime/` or `core_memory/integrations/api.py`
imports from the `openclaw_` prefix.

---

## Sub-task 9b — Rename event schema strings

**The deepest coupling.** `engine.py` and `flush_flow.py` embed OpenClaw's namespace
directly in Core Memory's event schema identifiers:

```python
"schema": "openclaw.memory.flush_report.v1"
"schema": "openclaw.memory.flush_checkpoint.v1"
"schema": "openclaw.memory.crawler_update.v1"
```

These should be Core Memory's own event format:

```python
"schema": "core-memory.flush_report.v1"
"schema": "core-memory.flush_checkpoint.v1"
"schema": "core-memory.crawler_update.v1"
```

**Migration path** (this is a breaking change for any consumer parsing schema strings):
1. Define constants in `core_memory/runtime/event_schemas.py`:
   ```python
   FLUSH_REPORT    = "core-memory.flush_report.v1"
   FLUSH_CHECKPOINT = "core-memory.flush_checkpoint.v1"
   CRAWLER_UPDATE  = "core-memory.crawler_update.v1"
   # Aliases for backward compat (consumers reading old events)
   FLUSH_REPORT_LEGACY    = "openclaw.memory.flush_report.v1"
   FLUSH_CHECKPOINT_LEGACY = "openclaw.memory.flush_checkpoint.v1"
   CRAWLER_UPDATE_LEGACY  = "openclaw.memory.crawler_update.v1"
   ```
2. Replace all hardcoded string literals in `engine.py` and `flush_flow.py` with the
   constants.
3. Where Core Memory reads schema strings back (event dispatch, filtering), accept both
   the canonical and legacy values. Document the legacy aliases as deprecated.
4. Update `integrations/openclaw/` (after Step 9c) to emit and read the new strings.

---

## Sub-task 9c — Move OpenClaw files into `integrations/openclaw/`

Create `core_memory/integrations/openclaw/__init__.py` and move all flat openclaw files:

| Current path | New path |
|---|---|
| `integrations/openclaw_agent_end_bridge.py` | `integrations/openclaw/agent_end_bridge.py` |
| `integrations/openclaw_compaction_bridge.py` | `integrations/openclaw/compaction_bridge.py` |
| `integrations/openclaw_compaction_queue.py` | `integrations/openclaw/compaction_queue.py` |
| `integrations/openclaw_flags.py` | `integrations/openclaw/flags.py` (only `supersede_openclaw_summary_enabled`) |
| `integrations/openclaw_onboard.py` | `integrations/openclaw/onboard.py` |
| `integrations/openclaw_read_bridge.py` | `integrations/openclaw/read_bridge.py` |
| `integrations/openclaw_runtime.py` | `integrations/openclaw/runtime.py` |

After moving, update all imports. Add backward-compat re-exports in the old locations
for one release cycle (e.g., `integrations/openclaw_flags.py` becomes a one-liner
`from .openclaw.flags import supersede_openclaw_summary_enabled`), then delete them.

Also: `association/crawler_contract.py` references openclaw concepts. The fix here is
to define an abstract protocol — `CrawlerContract` — in `association/crawler_contract.py`
that OpenClaw's implementation satisfies, rather than `association/` importing from
`integrations/openclaw/` by name. The openclaw integration should *implement* the
contract, not be imported by it.

---

## Sub-task 9d — Move CLI files into `core_memory/cli/`

The entry point `core_memory.cli:main` (declared in `pyproject.toml`) resolves to
`core_memory/cli/__init__.py:main` when `cli/` is a package — no `pyproject.toml` change
needed.

**Directory structure after:**
```
core_memory/cli/
    __init__.py          # main() lives here (was cli.py)
    compat.py            # was cli_compat.py
    diagnostics.py       # was cli_diagnostics.py
    parsers/
        __init__.py
        extended.py      # was cli_parser_extended.py
        memory.py        # was cli_parser_memory.py
        ops.py           # was cli_parser_ops.py
    handlers/
        __init__.py
        graph.py         # was cli_handlers_graph.py
        integrations.py  # was cli_handlers_integrations.py
        memory.py        # was cli_memory_handlers.py
        metrics.py       # was cli_handlers_metrics.py
        ops.py           # was cli_handlers_ops.py
        semantic.py      # was cli_handlers_semantic.py
        store.py         # was cli_handlers_store.py
```

**Process:**
1. Create the `cli/` package structure.
2. Move files one at a time, updating internal `from .cli_handlers_X import` references
   in `cli.py` as you go.
3. Move `cli.py` last — rename to `cli/__init__.py` and update any remaining relative
   imports.
4. Verify `python -m core_memory.cli --help` works.
5. Verify `core-memory --help` (entry point) works.
6. Delete original top-level CLI files.

---

## Sub-task 9e — Move Dreamer to `runtime/dreamer/`

`dreamer.py` at the top level is the execution logic (the "Move 37" association analysis).
It is imported by:
- `core_memory/runtime/side_effect_queue.py` as `from core_memory import dreamer`
- `core_memory/persistence/store_dream_bootstrap_ops.py` as `from core_memory import dreamer`

`runtime/dreamer_candidates.py` and `runtime/dreamer_eval.py` are already in `runtime/`.

**Target structure:**
```
core_memory/runtime/dreamer/
    __init__.py          # re-exports run_dreamer_pass (the public entry point)
    analysis.py          # was core_memory/dreamer.py
    candidates.py        # was runtime/dreamer_candidates.py
    eval.py              # was runtime/dreamer_eval.py
```

Update imports:
- `runtime/side_effect_queue.py`: `from core_memory import dreamer` →
  `from core_memory.runtime.dreamer import dreamer as dreamer_module` (or restructure
  the call to import `run_dreamer_pass` directly)
- `persistence/store_dream_bootstrap_ops.py`: same pattern
- `cli_handlers_ops.py` (will be in `cli/handlers/ops.py` after 9d): update import
- `cli_handlers_metrics.py` (will be in `cli/handlers/metrics.py`): update import

Also move `runtime/longitudinal_benchmark.py` → `eval/longitudinal_benchmark.py` (it
is already referenced from `eval/longitudinal_benchmark_v2.py`; consolidating into `eval/`
is the honest location). Update the `cli/handlers/metrics.py` import.

---

## Sub-task 9f — Reorganize `runtime/` into subdirectories

Do this step last within Phase 9. All prior steps reduce the number of files at
`runtime/`'s root, making this reorganization smaller.

**Target structure:**
```
core_memory/runtime/
    __init__.py              # keep empty (lazy-load design; see Phase 2)
    engine.py                # stays at root — visible orchestrator entry point
    state.py                 # stays at root — shared runtime state
    myelination.py           # stays at root — standing memory policy
    agent_authored_contract.py  # stays — cross-cutting contract
    agent_crawler_invoke.py     # stays — cross-cutting invocation
    event_schemas.py            # new (from 9b) — canonical event schema constants

    turn/
        __init__.py
        ingress.py           # was runtime/ingress.py
        flow.py              # was runtime/turn_flow.py
        prep.py              # was runtime/turn_prep.py
        archive.py           # was runtime/turn_archive.py
        quality.py           # was runtime/turn_quality.py
        enrichment.py        # was runtime/enrichment.py
        session_delta.py     # was runtime/session_enrichment_delta.py

    flush/
        __init__.py
        flow.py              # was runtime/flush_flow.py
        state.py             # was runtime/flush_state.py

    session/
        __init__.py
        start_flow.py        # was runtime/session_start_flow.py
        surface.py           # was runtime/session_surface.py
        live.py              # was runtime/live_session.py

    passes/
        __init__.py
        association.py       # was runtime/association_pass.py
        decision.py          # was runtime/decision_pass.py
        goal_lifecycle.py    # was runtime/goal_lifecycle.py

    queue/
        __init__.py
        side_effect_queue.py # was runtime/side_effect_queue.py
        side_effects.py      # was runtime/side_effects.py
        jobs.py              # was runtime/jobs.py
        worker.py            # was runtime/worker.py
        compaction_queue.py  # was runtime/compaction_queue.py

    dreamer/
        __init__.py          # from 9e
        analysis.py          # from 9e (was core_memory/dreamer.py)
        candidates.py        # from 9e
        eval.py              # from 9e

    observability/
        __init__.py
        observability.py     # was runtime/observability.py
        retrieval_feedback.py   # was runtime/retrieval_feedback.py
        value_overrides.py   # was runtime/retrieval_value_overrides.py
        reviewer_value.py    # was runtime/reviewer_quick_value.py
```

**Process:** Move files subdirectory by subdirectory. Each `__init__.py` re-exports the
same symbols the old module exported, so callers that import
`from core_memory.runtime.turn_flow import X` can migrate incrementally via the re-export
shim. Remove shims in the next minor version.

---

## Sub-task 9g — Thin `integrations/api.py`

Currently imports from 12+ internal modules (claim, entity, runtime, retrieval,
write_pipeline, persistence, openclaw_flags, schema). A true stable port should depend
only on `core_memory.__init__`'s public surface.

**Process:**
1. Audit every import in `integrations/api.py`. For each, determine if the symbol is
   already in `core_memory.__init__.__all__`. If yes, switch to importing from
   `core_memory` directly. If not, either add it to `__init__.__all__` (if it should be
   public) or justify the internal access with a comment.
2. Functions in `integrations/api.py` that reach directly into internal modules for
   convenience (e.g., calling `claim.resolver.resolve_all_current_state` rather than
   going through `recall()`) should be re-examined. If the public API doesn't provide the
   access needed, that is a signal to extend the public API, not to deepen the internal
   coupling.
3. The goal is: all imports in `integrations/api.py` resolve to either `core_memory.*`
   (public) or standard library. No `core_memory.runtime.*`, `core_memory.claim.*`, etc.

This step may reveal gaps in the public API that need filling before it can be completed.
Those gaps become additions to `core_memory/__init__.__all__` in a prior commit.

---

## Guard rails

- **One sub-task per PR.** Do not batch 9c and 9d together. Each PR is fully testable in
  isolation.
- **Add re-export shims** at old import paths before deleting old files. This keeps any
  external code that imports from old paths working through a deprecation window.
- **Run `grep -r "from core_memory"` across the entire repo** (including `tests/`,
  `eval/`, `demo/`) before and after each step to confirm nothing outside the package
  broke.
- **The event schema string change (9b) requires coordination with the OpenClaw
  integration.** Do not ship 9b to production without updating the OpenClaw consumer that
  reads these schema strings.
