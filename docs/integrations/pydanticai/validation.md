# PydanticAI Validation

Status: Canonical

## Core checks
```bash
python -m unittest tests.test_memory_search_tool_wrapper
python -m unittest tests.test_memory_execute_contract
python -m unittest tests.test_pydanticai_adapter
python -m unittest tests.test_pydanticai_memory_tools
python eval/memory_execute_eval.py
```

## Hydration / archive checks
```bash
python -m unittest tests.test_turn_archive_slice1
python -m unittest tests.test_turn_archive_get_turn_slice2
python -m unittest tests.test_turn_hydration_slice3
```

## Flag/guard checks
```bash
python -m unittest tests.test_openclaw_flags_slice5
python -m unittest tests.test_supersession_guards_slice6
```

## Validate for
- deterministic runtime output
- stable finalized-turn ingestion
- transcript archive append/index behavior
- hydration surfaces return expected turn/tool/adjacent payloads
- flag guards behave safely (disable/no-op paths)
- high answerable rates
- low warning rates
- strong causal grounding where expected

## Shared references
- `../shared/validation-common.md`
- `../../contracts/http_api.v1.json` (for parity awareness even though PydanticAI is in-process)
