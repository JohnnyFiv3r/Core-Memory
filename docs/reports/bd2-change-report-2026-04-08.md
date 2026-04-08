# BD-2 Change Report (2026-04-08)

## Scope
BD-2 focused on decomposing `core_memory/persistence/store.py` into focused helper modules while preserving external behavior and test stability.

This report summarizes the full ticket arc through PR #95.

---

## Baseline vs current

- **Baseline at BD-2 start:** `store.py` ~1996 lines
- **Current after PR #95:** `store.py` ~77 lines
- **Net reduction:** ~1919 lines (**~96% reduction**)

Design outcome:
- `MemoryStore` is now a thin composition shell.
- Core logic moved into helper modules and mixins.
- Existing public API surface is retained via delegation/re-export.

---

## Major delivered slices (chronological)

### Core BD-2 decomposition slices
- **#68** metrics runtime state extraction
- **#69** rationale scoring extraction
- **#70** add-bead helper heuristics extraction
- **#71** bead validation helper extraction
- **#72** constraints helper extraction
- **#73** query/session helper extraction
- **#74** relationship helper extraction
- **#75** compaction helper extraction
- **#76** context-retrieval helper extraction
- **#77** core `add_bead` write-path extraction
- **#78** projection rebuild helper extraction
- **#79** autonomy KPI/reinforcement extraction
- **#85** heads/index update helper extraction
- **#86** bootstrap + dream helper extraction
- **#87** failure-pattern helper extraction
- **#88** init/config helper extraction
- **#89** json IO + enum normalization helper extraction
- **#90** query-intent + hygiene wrapper extraction
- **#91** promotion scoring/service wrapper extraction
- **#92** lifecycle helper extraction
- **#93** shared constants + `DiagnosticError` contract extraction
- **#94** reporting/promotion facade mixin extraction
- **#95** core facade mixin extraction (final large consolidation)

### Interleaved correction tranche (requested before rebase)
- **#80** #53 schema round-trip compatibility fixes
- **#81** P1 fixes: canonical Dreamer apply path + queue multi-writer safety
- **#82** P2 fixes: semantic doctor connectivity truthfulness, reviewer path canonicalization, benchmark framing
- **#83** hardening follow-up tests/docs
- **#84** docs alignment / rebase-prep cleanup

---

## New/expanded module structure introduced by BD-2

### Persistence helpers
- `store_add_bead_ops.py`
- `store_add_helpers.py`
- `store_validation_helpers.py`
- `store_constraints.py`
- `store_query.py`
- `store_session_ops.py`
- `store_relationship_ops.py`
- `store_compaction_ops.py`
- `store_projection_ops.py`
- `store_failure_ops.py`
- `store_init_ops.py`
- `store_json_ops.py`
- `store_lifecycle_ops.py`
- `store_contract.py`
- `store_promotion_ops.py`
- `store_text_hygiene_ops.py`

### Persistence mixins
- `store_reporting_promotion_mixin.py`
- `store_core_delegates_mixin.py`

### Reporting helpers used by decomposition
- `core_memory/reporting/store_metrics_runtime.py`
- `core_memory/reporting/store_rationale.py`

---

## Quality/validation summary

- Continuous targeted delegation tests were added for each extraction slice.
- Full suite remained green through the sequence (latest observed in this arc: **539 tests, OK, skipped=29**).
- Behavior-sensitive correction items were handled before continuing large decomposition work.

---

## Compatibility notes

- Public `MemoryStore` method signatures preserved; implementation moved behind delegates/mixins.
- Constants and `DiagnosticError` moved to `store_contract.py` and re-exported via `store.py` for compatibility.
- Schema deserialization round-trip regressions from #53 addressed in #80.

---

## Remaining recommendation

From here, priority should be:
1. Merge outstanding stacked PRs in strict order.
2. After merge, run one post-merge regression sweep on `master`.
3. Optionally follow with a low-risk cleanup pass (import tidying + contributor docs consolidation).
