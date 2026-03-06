# SpringAI Validation

Status: Canonical

## Minimum validation steps

### HTTP contract checks
```bash
python -m unittest tests.test_http_ingress
```

Covers:
- turn-finalized emit
- idempotent same-turn behavior
- runtime `/execute`
- `/classify-intent`
- auth protection
- runtime determinism for execute
- pin-field pass-through for reason

### Runtime memory quality checks
```bash
python eval/memory_execute_eval.py
```

Key metrics:
- `non_empty_results_rate`
- `anchor_presence_rate`
- `warning_rate`
- `answerable_rate`
- `causal_grounding_achieved_rate`
- `causal_strong_grounding_rate`

## Expected healthy characteristics
- non-empty results near 1.0 on standard suites
- warning rate low
- causal grounding achieved high
- strong grounding tracked separately from simple grounding coverage
