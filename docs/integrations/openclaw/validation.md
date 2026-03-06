# OpenClaw Validation

Status: Canonical

## Core checks
```bash
python -m unittest tests.test_memory_execute_contract
python -m unittest tests.test_memory_reason_pins
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
