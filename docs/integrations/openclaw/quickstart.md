# OpenClaw Quickstart

Status: Canonical
See also:
- `README.md`
- `integration-guide.md`
- `api-reference.md`
- `../shared/contracts.md`

## Goal
Run Core Memory as the primary memory/runtime system inside OpenClaw.

## 1) Install / run OpenClaw + Core Memory repo
Use the repository root as the working workspace for the main agent.

## 2) Verify canonical surfaces
- `docs/canonical_surfaces.md`
- `docs/contracts/http_api.v1.json`

## 3) Use current memory surfaces
Primary runtime surface:
- `core_memory.retrieval.tools.memory.execute`

Canonical retrieval family (when you need direct calls):
- `memory.search`
- `memory.trace`
- `memory.execute`

Continuity/context injection:
- `load_continuity_injection(...)`

Hydration (optional, post-selection):
- `hydrate_bead_sources(...)`

Primary write-path ingestion:
- `emit_turn_finalized(...)`

## 4) Validate
```bash
python -m unittest tests.test_memory_execute_contract
python -m unittest tests.test_openclaw_read_bridge
python eval/memory_execute_eval.py
```

## 5) Contributor orientation
Start with:
- `../shared/concepts.md`
- `integration-guide.md`
