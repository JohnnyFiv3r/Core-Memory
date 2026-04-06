# Reviewer Start Here

Status: Canonical reviewer guide

## 5-minute path
1. Run `PYTHONPATH=. python3 examples/canonical_5min.py`
2. Run `PYTHONPATH=. python3 examples/proof_policy_reuse.py`
3. Run `PYTHONPATH=. python3 eval/longitudinal_learning_eval.py`
4. Then read `docs/canonical_surfaces.md` and `docs/architecture_overview.md`

## Fastest evaluation routes

### A) Product surface clarity
- `docs/canonical_surfaces.md`
- `docs/public_surface.md`
- `docs/contracts/http_api.v1.json`

### B) Core architecture coherence
- `docs/architecture_overview.md`
- `docs/canonical_paths.md`
- `docs/core_adapters_architecture.md`

### C) Adapter ergonomics
- OpenClaw: `docs/integrations/openclaw/README.md`
- PydanticAI: `docs/integrations/pydanticai/README.md`
- SpringAI/HTTP: `docs/integrations/springai/README.md`
- LangChain: `docs/integrations/langchain/README.md`

## Where current architecture is strongest
- explicit finalized-turn ingest contract
- canonical retrieval family (`search` / `trace` / `execute`)
- explicit hydration boundary
- strong adapter parity direction with practical integration docs

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

## Useful links
- `README.md`
- `eval/longitudinal_learning_eval.py`
- `docs/concepts/why-core-memory.md`
- `docs/architecture_overview.md`
- `docs/integrations/`
- `examples/canonical_5min.py`
- `examples/quickstart.py`
- `examples/proof_carry_forward.py`
- `examples/proof_policy_reuse.py`
- `examples/store_compat_quickstart.py` (compatibility/direct-store reference)
- `examples/pydanticai_basic.py`
- `examples/pydanticai_demo_roundtrip.py` (canonical run_with_memory + execute/search/trace roundtrip)
- `examples/pydanticai_live_demo.py`
- `examples/pydanticai_with_memory.py` (behavior-change proof via durable memory)
