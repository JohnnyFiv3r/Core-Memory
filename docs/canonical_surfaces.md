# Canonical Surfaces

Status: Canonical

Purpose: inventory the current public, supported surfaces of Core Memory without changing runtime behavior.

## Canonical runtime APIs

### Continuity injection surface
- `core_memory.continuity_injection.load_continuity_injection(...)`

Authority order:
1. `rolling-window.records.json` (authoritative continuity surface)
2. `promoted-context.meta.json` (fallback metadata only)
3. empty

Non-authoritative continuity artifacts:
- `promoted-context.md` (derived operator-facing text)

### Unified memory skill surface
- `core_memory.tools.memory.execute`
- `core_memory.tools.memory.search`
- `core_memory.tools.memory.reason`
- `core_memory.tools.memory.get_search_form`

These are the preferred tool-facing entry points for runtime retrieval/reasoning.

### Finalized-turn ingestion
- `core_memory.integrations.api.emit_turn_finalized(...)`

This is the canonical write-path port for orchestrator integrations.

## Canonical HTTP surfaces

Served by:
- `core_memory.integrations.http.server`

Endpoints:
- `GET /healthz`
- `POST /v1/memory/turn-finalized`
- `POST /v1/memory/classify-intent`
- `GET /v1/memory/search-form`
- `POST /v1/memory/search`
- `POST /v1/memory/reason`
- `POST /v1/memory/execute`

Canonical machine-readable contract:
- `docs/contracts/http_api.v1.json`

## Canonical CLI surfaces

Served by:
- `core_memory.cli`

Current canonical memory-related commands:
- `core-memory memory form`
- `core-memory memory search --typed ...`
- `core-memory memory execute --request ...`
- `core-memory reason <query>`
- `core-memory graph ...`
- `core-memory metrics ...`

## Canonical integration guides

Current canonical docs:
- `docs/integrations/springai/quickstart.md`
- `docs/integrations/springai/integration-guide.md`
- `docs/integrations/openclaw/integration-guide.md`
- `docs/integrations/pydanticai/integration-guide.md`
- `docs/core_adapters_architecture.md`
- `docs/integrations/shared/README.md` (supporting overview)
- `docs/memory_search_skill.md`
- `docs/memory_search_agent_playbook.md`

Transitional stub retained:
- `docs/springai_adapter.md`

## Canonical evaluation entry points

- `eval/memory_execute_eval.py`
- `eval/memory_search_ab_compare.py`
- `eval/memory_search_smoke.py`
- `eval/paraphrase_eval.py`
- `eval/retrieval_eval.py`

## Supported but secondary / lower-level surfaces

These are useful but not the preferred first interface for contributors:
- `core_memory.memory_skill.*` internals
- `core_memory.tools.memory_search.*`
- `core_memory.tools.memory_reason.memory_reason`
- retrieval internals in `core_memory/retrieval/*`

## Transitional / compatibility surfaces

These remain for compatibility or migration support and should not be treated as the primary public interface:
- `core_memory.store.migrate_legacy_store(...)`
- CLI `core-memory migrate-store`
- historical migration/archive docs under `docs/archive/`

## Historical artifacts

The following are historical snapshots, not living specs:
- dated reports in `docs/archive/reports/2026-03-05/`
- archived migration/deprecation planning docs in `docs/archive/`

## Contributor rule of thumb

If choosing where to integrate first:
1. runtime retrieval/reasoning -> `core_memory.tools.memory.*`
2. write-path ingestion -> `emit_turn_finalized(...)`
3. JVM/remote integration -> HTTP contract in `docs/contracts/http_api.v1.json`
