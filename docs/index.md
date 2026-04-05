# Documentation Index

Status: Canonical navigation

Use this page as the authoritative map of current docs.

## Start here
- [`../README.md`](../README.md)
- [`reviewers/start-here.md`](reviewers/start-here.md)
- [`canonical_surfaces.md`](canonical_surfaces.md)
- [`public_surface.md`](public_surface.md)

## Concepts
- [`concepts/why-core-memory.md`](concepts/why-core-memory.md)
- [`memory_surfaces_spec.md`](memory_surfaces_spec.md)
- [`truth_hierarchy.md`](truth_hierarchy.md)
- [`truth_hierarchy_policy.md`](truth_hierarchy_policy.md)

## Architecture
- [`architecture_overview.md`](architecture_overview.md)
- [`ARCHITECTURE.md`](ARCHITECTURE.md)
- [`canonical_paths.md`](canonical_paths.md)
- [`integration_contract.md`](integration_contract.md)
- [`core_adapters_architecture.md`](core_adapters_architecture.md)

## Adapters
- Shared foundations:
  - [`integrations/shared/README.md`](integrations/shared/README.md)
  - [`integrations/shared/concepts.md`](integrations/shared/concepts.md)
  - [`integrations/shared/contracts.md`](integrations/shared/contracts.md)
- OpenClaw: [`integrations/openclaw/README.md`](integrations/openclaw/README.md)
- PydanticAI: [`integrations/pydanticai/README.md`](integrations/pydanticai/README.md)
- SpringAI / HTTP: [`integrations/springai/README.md`](integrations/springai/README.md)
- LangChain: [`integrations/langchain/README.md`](integrations/langchain/README.md)
- Neo4j (shadow graph): [`integrations/neo4j/README.md`](integrations/neo4j/README.md)

## Integration quickstarts
- [`integrations/openclaw/quickstart.md`](integrations/openclaw/quickstart.md)
- [`integrations/springai/quickstart.md`](integrations/springai/quickstart.md)
- [`integrations/pydanticai/quickstart.md`](integrations/pydanticai/quickstart.md)
- [`integrations/langchain/quickstart.md`](integrations/langchain/quickstart.md)
- [`integrations/neo4j/quickstart.md`](integrations/neo4j/quickstart.md)

## Contracts
- [`contracts/http_api.v1.json`](contracts/http_api.v1.json)
- [`contracts/canonical_phase_function_map.md`](contracts/canonical_phase_function_map.md)
- [`adapter_parity_matrix.md`](adapter_parity_matrix.md)

## Specs
- [`specs/association-inference-v2.1.md`](specs/association-inference-v2.1.md)
- [`specs/agent-authored-turn-memory-v1.md`](specs/agent-authored-turn-memory-v1.md)

## Evaluation entrypoints
- `../eval/memory_execute_eval.py`

## Evaluation entrypoints
- `../eval/memory_execute_eval.py`

## Compatibility / historical material
- [`archive/`](archive/) — superseded migration/process/history docs
- [`reports/`](reports/) — dated snapshots and audits
- Legacy typed-search/search-form narratives are treated as historical material, not forward path docs.

## Notes for reviewers
Canonical retrieval story is:
- `search` (anchor retrieval)
- `trace` (causal traversal)
- `execute` (single orchestrated entrypoint)

Hydration is explicit post-selection source recovery.
Deep recall is real, but separate from canonical hydration.
