# V2-P13 Closeout Checklist

Status: Complete

## Scope
Single-judgment authority cutover:
- remove store-level canonical quick-association authorship
- demote deterministic worker promotion judgment
- enforce crawler-reviewed canonical promotion/association authority
- lock pass_engine as non-authoritative
- introduce canonical event_* naming surfaces

## Completion checklist
- [x] `store.add_bead(...)` quick associations are preview-only
- [x] deterministic worker promotion no longer mutates canonical promotion state
- [x] authority invariants added (`tests/test_p13_authority_enforcement.py`)
- [x] `association/pass_engine.py` explicitly marked deprecated + non-authoritative
- [x] canonical event naming surfaces added (`event_ingress`, `event_worker`, `event_state`)
- [x] core imports switched to canonical event_* surfaces
- [x] Step 6 sweep completed

## Regression evidence
Command:

```bash
python3 -m unittest \
  tests.test_p13_authority_enforcement \
  tests.test_event_module_aliases \
  tests.test_sidecar_worker \
  tests.test_association_crawler_contract \
  tests.test_memory_engine \
  tests.test_openclaw_integration \
  tests.test_live_session_authority \
  tests.test_e2e_program_scenarios \
  tests.test_adapter_contract_markers \
  tests.test_pydanticai_adapter \
  tests.test_p10_deprecation_markers -v
```

Result:
- 27 passed / 0 failed

## Final authority stance (post-P13)
- Canonical promotion/association decisions: crawler-reviewed path
- Store deterministic association output: preview-only, non-authoritative
- Worker deterministic promotion output: preview-only, non-authoritative
- Canonical runtime naming: `event_*` surfaces (legacy `sidecar_*` retained as compatibility-backed implementation during transition)
