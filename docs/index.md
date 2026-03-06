# Documentation Index

Status: Canonical

Start here for current Core Memory documentation.

## Architecture and canonical interfaces
- `canonical_surfaces.md` — current supported public surfaces
- `core_adapters_architecture.md` — integration architecture overview
- `contracts/http_api.v1.json` — canonical HTTP/API contract artifact

## Integration guides
- `springai_adapter.md` — SpringAI write-path + runtime tool integration
- `integration/core-adapters.md` — adapter overview across orchestrators
- `memory_search_skill.md` — memory skill runtime surface
- `memory_search_agent_playbook.md` — agent-side usage guidance

## Validation and evaluation
- `../eval/memory_execute_eval.py`
- `../eval/memory_search_ab_compare.py`
- `../eval/memory_search_smoke.py`
- `../eval/paraphrase_eval.py`
- `../eval/retrieval_eval.py`

## Historical / snapshot material
- `archive/` — archived migration/deprecation/history docs
- dated `*_2026-03-05.md` reports in `docs/` — point-in-time evaluation artifacts

## Suggested reading order for contributors
1. `canonical_surfaces.md`
2. `contracts/http_api.v1.json`
3. `springai_adapter.md` or relevant integration guide
4. validation/eval scripts if changing behavior
