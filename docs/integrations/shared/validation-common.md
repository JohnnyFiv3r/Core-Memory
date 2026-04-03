# Shared Validation

Status: Canonical

## Core validation goals
- deterministic runtime outputs
- idempotent finalized-turn ingestion
- low warning rates
- high answerable rates
- strong causal grounding where expected

## Useful scripts
- `../../eval/memory_execute_eval.py`
- `../../eval/memory_search_ab_compare.py`
- `../../eval/memory_search_smoke.py`
- `../../eval/paraphrase_eval.py`
- `../../eval/retrieval_eval.py`

## Useful tests
- `tests.test_http_ingress`
- `tests.test_memory_execute_contract`
- `tests.test_memory_execute_causal_fallback`
- `tests.test_memory_search_tool_wrapper`
- `tests.test_temporal_only_grounding_guard`
- `tests.test_canonical_hydration_contract`

## Key runtime metrics
- `non_empty_results_rate`
- `anchor_presence_rate`
- `warning_rate`
- `answerable_rate`
- `causal_grounding_achieved_rate`
- `causal_strong_grounding_rate`

## Determinism checks
At minimum:
- same request -> same ordering
- same request -> same snapped fields
- same request -> same confidence / warnings / next_action
- same write event idempotency by `session_id:turn_id`
