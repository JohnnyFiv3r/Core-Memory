# Documentation Index

Status: Canonical

Start here for current Core Memory documentation.

## Architecture and canonical interfaces
- `canonical_surfaces.md` — current supported public surfaces
- `core_adapters_architecture.md` — integration architecture overview
- `contracts/http_api.v1.json` — canonical HTTP/API contract artifact
- `transition_roadmap_locked.md` — locked transition execution roadmap
- `phase1_closeout_checklist.md` — phase 1 readiness checklist
- `schema_inventory_baseline.md` — phase 2 schema baseline
- `schema_canonical_spec.md` — canonical schema specification
- `phase2_closeout_checklist.md` — phase 2 readiness checklist
- `phase3_trigger_model_progress.md` — phase 3 trigger-model convergence progress
- `phase3_closeout_checklist.md` — phase 3 readiness checklist
- `phase4_internalization_progress.md` — phase 4 internalization progress

## Integration guides
- `integrations/springai/quickstart.md` — SpringAI integration start point
- `integrations/openclaw/quickstart.md` — OpenClaw integration start point
- `integrations/pydanticai/quickstart.md` — PydanticAI integration start point
- `integration/core-adapters.md` — adapter overview across orchestrators (supporting)
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
3. relevant integration quickstart under `integrations/`
4. validation/eval scripts if changing behavior
