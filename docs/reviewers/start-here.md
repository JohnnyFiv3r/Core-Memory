# Reviewer Start Here

Status: Canonical reviewer guide

## 5–10 minute value path (run this first)

1) **Canonical** onboarding path

```bash
core-memory --root ./memory setup init
CORE_MEMORY_CANONICAL_SEMANTIC_MODE=degraded_allowed PYTHONPATH=. python3 examples/canonical_5min.py
```

2) **Recommended** behavior proof (memory changes a later answer)

```bash
PYTHONPATH=. python3 examples/proof_carry_forward.py
```

3) **Recommended** quick value eval v2 (canonical write + retrieval + repeated-incident + Dreamer transfer)

```bash
PYTHONPATH=. python3 -m eval.reviewer_quick_value_v2 --root ./memory --strict
```

Expected signal:
- `overall.quick_value_passed: true`
- `steps.repeated_incident_improvement.improved: true`
- `steps.dreamer_transfer_improvement.improved: true`

## Then inspect contracts and architecture

- `README.md`
- `docs/contributor_map.md`
- `docs/public_surface.md`
- `docs/canonical_surfaces.md`
- `docs/architecture_overview.md`
- `docs/canonical_paths.md`

## Adapter docs (after value check)

- OpenClaw: `docs/integrations/openclaw/README.md`
- PydanticAI: `docs/integrations/pydanticai/README.md`
- SpringAI/HTTP: `docs/integrations/springai/README.md`
- LangChain: `docs/integrations/langchain/README.md`

## Example labels (audience + contract level)

- `examples/canonical_5min.py` — **Canonical** (first-touch onboarding)
- `examples/quickstart.py` — **Canonical** (expanded onboarding)
- `examples/proof_carry_forward.py` — **Recommended** (behavior proof)
- `eval/reviewer_quick_value_v2.py` — **Recommended** (5-10 minute quick-value walkthrough)
- `examples/pydanticai_basic.py` — **Recommended** (adapter quick smoke)
- `examples/pydanticai_demo_roundtrip.py` — **Recommended** (canonical run_with_memory + execute/search/trace roundtrip)
- `examples/http_springai_client.py` — **Recommended** (HTTP adapter path)
- `examples/openclaw_bridge_demo.py` — **Recommended** (OpenClaw adapter path)
- `examples/store_compat_quickstart.py` — **Compatibility** (direct-store workflows)
- `examples/pydanticai_live_demo.py` — **Experimental** (live-provider dependent)
- `examples/pydanticai_with_memory.py` — **Experimental** (deeper adapter experimentation)

## Known limitations (honest view)
- optional dependencies (e.g., semantic backends) may change runtime quality/profile by environment
- some adapter surfaces are lighter-weight than others (especially newer integrations)
- evaluation breadth exists, but not all reviewer questions are answered by one benchmark pack today
- ideas not represented in canonical docs should be treated as not-yet-integrated, not implied roadmap commitments

## Feedback we especially want
- conceptual clarity and contract boundaries
- retrieval/hydration semantics and naming precision
- adapter API ergonomics (especially LangChain/PydanticAI)
- docs clarity around canonical vs compatibility/historical material
