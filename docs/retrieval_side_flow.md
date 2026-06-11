# Retrieval-side Flow

Status: Canonical

## Entry surfaces
- `core_memory.recall` (preferred — full orchestrator, returns `RecallResult`)
- `core_memory.retrieval.tools.memory.search` (low-level anchor retrieval)
- `core_memory.retrieval.tools.memory.trace` (low-level causal traversal)
- `core_memory.retrieval.tools.memory.execute` (low-level unified request)

## Retrieval behavior (`recall`)
1. Canonical request normalization (effort tier selects k / hops / grounding mode)
2. Semantic + hybrid anchor retrieval over the visible corpus (session +
   rolling window always consulted first)
3. Association-hop expansion over the causal graph — every effort tier
   (low = 1 hop; medium/high add multi-source seeding); edge scores weighted
   by relationship type, provenance, confidence, and lifecycle
   (reinforcement/decay/supersession — see `edge_lifecycle.md`)
4. Causal attribution pipeline when triggered by declared intent, classified
   intent, or causal-edge density in the evidence
   (`result.metadata.causal_pipeline_trigger`)
5. Grounded result assembly with confidence/next_action metadata; contributing
   edges logged for flush-time reinforcement

## Rules
- Agents and adapters use `recall` — it is the only read surface that feeds
  retrieval telemetry and edge reinforcement.
- Low-level surfaces are for inspection/tooling; compatibility shims are
  non-canonical and should not be a first integration target.
