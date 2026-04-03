# OpenClaw Validation

Status: Canonical

## Core checks
```bash
python -m unittest tests.test_memory_execute_contract
python -m unittest tests.test_temporal_only_grounding_guard
python -m unittest tests.test_http_ingress
python eval/memory_execute_eval.py
```

## Watch for
- deterministic execute behavior
- warning rate staying low
- answerable rate staying high
- causal grounding staying high
- strong grounding tracked separately from binary grounding coverage

## Useful metrics
- `answerable_rate`
- `answerable_rate_non_causal`
- `warning_rate`
- `causal_grounding_achieved_rate`
- `causal_strong_grounding_rate`

## Bridge CI smoke gate (synthetic finalized turn)
Use this in CI/containerized verification to assert append-path health without requiring live chat traffic:

```bash
./scripts/openclaw_bridge_ci_smoke.sh
```

Pass criteria:
- `.beads/events/memory-events.jsonl` line count increases
- `.beads/events/memory-pass-status.jsonl` line count increases

This validates canonical event ingress append behavior at the bridge module boundary.
