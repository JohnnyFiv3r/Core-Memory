# Reviewer Start Here

Status: Canonical reviewer guide

## 5–10 minute value path (run this first)

1) Canonical onboarding path

```bash
core-memory --root ./memory setup init
CORE_MEMORY_CANONICAL_SEMANTIC_MODE=degraded_allowed PYTHONPATH=. python3 examples/canonical_5min.py
```

2) Behavior proof (memory changes a later answer)

```bash
PYTHONPATH=. python3 examples/proof_carry_forward.py
```

3) Quick eval result (memory changes a deployment choice)

```bash
PYTHONPATH=. python3 eval/reviewer_quick_value_eval.py
```

4) Longitudinal benchmark (learning over repeated episodes)

```bash
PYTHONPATH=. python3 eval/longitudinal_learning_eval.py
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

## Adapter docs (only after value check)

- OpenClaw: `docs/integrations/openclaw/README.md`
- PydanticAI: `docs/integrations/pydanticai/README.md`
- SpringAI/HTTP: `docs/integrations/springai/README.md`
- LangChain: `docs/integrations/langchain/README.md`

## Useful links

- `README.md`
- `examples/canonical_5min.py`
- `examples/proof_carry_forward.py`
- `examples/proof_policy_reuse.py`
- `eval/reviewer_quick_value_eval.py`
- `eval/longitudinal_learning_eval.py`
- `examples/quickstart.py`
- `examples/store_compat_quickstart.py` (advanced/compatibility reference)
- `examples/pydanticai_basic.py`
- `examples/pydanticai_demo_roundtrip.py` (canonical run_with_memory + execute/search/trace roundtrip)
- `examples/pydanticai_live_demo.py`
- `examples/pydanticai_with_memory.py` (behavior-change proof via durable memory)
