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

3) **Recommended** quick value eval (memory changes a deployment choice)

```bash
PYTHONPATH=. python3 eval/reviewer_quick_value_eval.py
```

Expected signal:
- `behavior_changed: true`
- `after_choice: "canary"`

## Then inspect contracts and architecture

- `README.md`
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
- `examples/pydanticai_basic.py` — **Recommended** (adapter quick smoke)
- `examples/pydanticai_demo_roundtrip.py` — **Compatibility** (direct-store/rolling-window internals)
- `examples/http_springai_client.py` — **Recommended** (HTTP adapter path)
- `examples/openclaw_bridge_demo.py` — **Recommended** (OpenClaw adapter path)
- `examples/store_compat_quickstart.py` — **Compatibility** (direct-store workflows)
- `examples/pydanticai_live_demo.py` — **Experimental** (live-provider dependent)
- `examples/pydanticai_with_memory.py` — **Experimental** (deeper adapter experimentation)
