# Reviewer Start Here

Status: Canonical reviewer guide

## 5-minute path
1. Read `README.md`
2. Read `docs/concepts/why-core-memory.md`
3. Read `docs/architecture_overview.md`
4. Check `docs/canonical_surfaces.md`
5. Pick one adapter doc folder under `docs/integrations/`

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
- `docs/concepts/why-core-memory.md`
- `docs/architecture_overview.md`
- `docs/integrations/`
- `examples/quickstart.py`
- `examples/pydanticai_basic.py`
- `examples/pydanticai_demo_roundtrip.py`
- `examples/pydanticai_live_demo.py`
- `examples/pydanticai_with_memory.py`
