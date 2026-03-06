# PydanticAI Validation

Status: Canonical

## Core checks
```bash
python -m unittest tests.test_memory_search_tool_wrapper
python -m unittest tests.test_memory_execute_contract
python eval/memory_execute_eval.py
```

## Validate for
- deterministic runtime output
- stable finalized-turn ingestion
- high answerable rates
- low warning rates
- strong causal grounding where expected

## Shared references
- `../shared/validation-common.md`
- `../../contracts/http_api.v1.json` (for parity awareness even though PydanticAI is in-process)
