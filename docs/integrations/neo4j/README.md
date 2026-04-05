# Neo4j Integration Docs

Status: Optional shadow-adapter docs

## Start here
- `quickstart.md`
- `integration-guide.md`
- `api-reference.md`
- `v2-visualization-contract.md` (planned visualization-oriented contract)

## Scope guardrail
Neo4j support in Core Memory is projection-only:
- used for visualization/inspection/offline query experiments
- not used for canonical writes
- not used for canonical `search` / `trace` / `execute`

Core Memory local state remains authoritative.

## Related canonical docs
- `../../canonical_surfaces.md`
- `../../integration_contract.md`
- `../../core_adapters_architecture.md`
- `../../../core_memory/integrations/neo4j/SHADOW_SCOPE.md`
