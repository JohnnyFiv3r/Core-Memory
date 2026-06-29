# Compatibility Ledger

**Status:** Canonical cleanup-governance reference

This ledger names compatibility surfaces that remain in the tree on purpose. It
does not make every historical path a forward API. It tells reviewers which
paths are public, which are private/internal, what replaces them, and what proof
is required before removal.

Compatibility removals must satisfy the row's removal condition and keep the
architecture guard baseline honest. When a row is repaired, shrink
`scripts/architecture_guards_baseline.json` in the same PR.

## Classification Rules

- **Public:** external users, adapters, scripts, or persisted data may depend on
  the surface. Removal requires a deprecation note or breaking-change window.
- **Private/internal:** first-party implementation detail. External callers
  should not use it, but current repo behavior or tests may still depend on it.
- **Artifact debt:** retained file or facade from an older shape. It should not
  gain new callers.

## Ledger

| Surface | Classification | Current role | Replacement path | Deprecation/removal condition | Proving test or gate |
|---|---|---|---|---|---|
| `core_memory/graph/api.py` | Public compatibility facade | Preserves legacy `core_memory.graph.api.*` imports while graph logic lives in split modules | `core_memory.graph.core`, `core_memory.graph.structural`, `core_memory.graph.traversal`, `core_memory.graph.semantic`, and package re-exports from `core_memory.graph` | Keep until a breaking-change/deprecation window has passed and `rg "core_memory.graph.api" core_memory tests docs` has no active callers except historical docs/ledger. Do not delete as a cleanup shortcut. | `pytest -m facade`, `tests/test_association_confidence_compat.py`, `scripts/check_architecture_guards.py --fail-on-new` |
| Top-level legacy CLI commands (`add`, `query`, `stats`, `doctor`, `heads`, `preflight`, `constraints`, `check-plan`, `retrieve-context`, `dream`, `rebuild`, `compact`, `consolidate`, `rolling-window`, `uncompact`, `myelinate`, `sidecar`, `openclaw`, `graph`, `metrics`, `async-jobs-*`, `dreamer-*`) | Public CLI compatibility | Keeps existing automation working while grouped command families become the forward surface | `setup`, `store`, `memory`, `recall`, `inspect`, `ingest`, `integrations`, `ops`, `semantic`, `mcp`, `myelination`, `dev` | Remove only in a major version or documented deprecation pass after docs, scripts, plugin config, and tests no longer call the alias. | `tests/test_cli_grouped_surface.py`, `tests/test_cli_taxonomy.py`, `tests/test_runtime_jobs_cli.py`, `tests/test_cli_compat_module.py` |
| `core_memory/cli/compat.py` | Private/internal compatibility adapter | Rewrites legacy argv shapes and maps grouped aliases to canonical handlers | Direct parser/handler dispatch in `core_memory/cli/__init__.py` and `core_memory/cli/handlers/*` | Remove only after the top-level CLI alias row is retired and no tests import `core_memory.cli.compat`. | `tests/test_cli_compat_module.py`, `tests/test_cli_grouped_surface.py` |
| Event schema legacy constants in `core_memory/runtime/event_schemas.py` (`*_LEGACY`) | Public persisted-data read compatibility | Lets readers accept historical `openclaw.memory.*` event schema strings without emitting them from new code | Canonical `core-memory.*` constants in the same module | Remove only after a migration/rebuild tool proves no supported store contains legacy event schema rows, or after a breaking-change window explicitly drops legacy event reads. | `tests/test_flush_report_artifact.py`, `tests/test_event_import_migration_guard.py`, targeted migration fixture before deletion |
| Retrieval request alias `form_submission` | Public request compatibility | Accepts older typed-search payloads in Python, HTTP, and adapter bridge callers | `request={...}` for `core_memory.retrieval.tools.memory.search`, `/v1/memory/search`, and adapter bridges; `recall(...)` as the primary read surface | Remove only after OpenClaw/PydanticAI/HTTP docs and tests use `request` only and a deprecation note exists for external callers. | `tests/test_memory_search_request_canonical.py`, `tests/test_memory_search_internal_isolation.py`, `tests/test_openclaw_read_bridge.py`, `tests/test_http_ingress.py` |
| `core_memory/retrieval/tools/memory_search.py` | Public compatibility wrapper | Preserves the older typed `search_typed(...)` import path and result envelope | `core_memory.retrieval.tools.memory.search(request=...)` or root alias `core_memory.memory_search(...)` | Remove only after adapter docs and validation no longer recommend `memory_search.py`, and package-root read surfaces remain covered. | `tests/test_memory_search_tool_wrapper.py`, `tests/test_package_root_public_surface.py`, `tests/test_pydanticai_memory_tools.py` |
| `core_memory/retrieval/trace.py` | Private compatibility wrapper | Keeps `trace_request(...)` delegating to the canonical trace implementation | `core_memory.retrieval.pipeline.canonical.trace_request` and `core_memory.retrieval.tools.memory.trace` | Remove when first-party imports are migrated and no external contract promises this module path. | `tests/test_retrieval_trace_compat.py`, `tests/test_trace_depth_api.py` |
| `core_memory/persistence/encryption.py` | Public optional compatibility module | Optional Fernet helpers for callers that imported encryption support directly. It is not part of the default write path. | Future explicit encrypted backend or documented storage encryption extension point | Do not delete as a dead-file cleanup. Removal requires a breaking-change process and replacement encryption story. | Import scan plus a dedicated encryption compatibility test before any removal |
| Persistence helper modules (`store_add_helpers.py`, `store_*_ops.py`, `promotion_service.py`) | Private/internal implementation | Store implementation and policy helpers, some still crossing architectural boundaries | Future post-write effects boundary owned by runtime; storage modules keep durable data operations | Do not delete wholesale. Boundary cleanup should move one side-effect cluster at a time and shrink guard allowlists after each repair. | Focused touched-module tests plus `python scripts/check_architecture_guards.py --fail-on-new` |
| Root flat-file relocation debt (`core_memory/cli_handlers_semantic.py`, `core_memory/management.py`, `core_memory/runtime/goal_lifecycle.py`, `core_memory/runtime/session_enrichment_delta.py`, `core_memory/runtime/source_envelope.py`) | Private/internal relocation debt | Active modules that violate the flat-file policy but still own live behavior | `core_memory/cli/handlers/*`, future management/runtime subpackages, and existing runtime subpackages such as `goals`, `session`, and `ingest` | Move only in narrow boundary slices that preserve public imports and shrink the guard baseline in the same PR. Do not delete to satisfy the flat-file rule. | `python scripts/check_architecture_guards.py --fail-on-new`, focused tests for touched modules |

## Recently Retired Artifacts

- `core_memory/persistence/store_core_delegates_mixin.py` and
  `core_memory/persistence/store_reporting_promotion_mixin.py` were retired after
  method inlining into `MemoryStore`. The proving gate was an active import scan
  for `StoreCoreDelegatesMixin|StoreReportingPromotionMixin`,
  `pytest -m mixin_assembly`, and an architecture-guard baseline shrink.
- `core_memory/retrieval/pipeline/explain.py` was retired after the live explain
  payload had already moved inline to `core_memory.retrieval.pipeline` and an
  active import scan for `retrieval.pipeline.explain|build_explain` returned no
  callers outside docs/ledger/release notes.
- `core_memory/persistence/write_ops.py` was retired after write behavior had
  already settled on `MemoryStore` methods and canonical runtime boundaries. The
  proving gate was an active import scan for `persistence.write_ops|write_ops`
  and focused package-root/write-path tests.

## Explicit Non-Compatibility

The presence of a file in historical reports under `docs/reports/` or
`docs/archive/` does not make it a supported surface. Current support requires a
row in this ledger or an entry in `docs/public_surface.md`.
