# Shared Integration Concepts

Status: Canonical
See also:
- `../../canonical_surfaces.md`
- `../../contracts/http_api.v1.json`
- `../../memory_search_skill.md`
- `../../memory_search_agent_playbook.md`

## Core ideas

Core Memory provides a deterministic memory operating layer for agents.

The current public memory surfaces are:
- `memory.execute` — unified request/response orchestrator
- `memory.search` — typed retrieval/search
- `memory.reason` — causal chain / grounded reasoning
- `memory.get_search_form` — machine-readable typed form
- `emit_turn_finalized(...)` — canonical finalized-turn write path

## Memory model
- **Transcript truth**: exact, recent, verbatim source of conversation state
- **Bead truth**: structured durable memory units
- **Archive truth**: historical snapshots and compacted memory records
- **Grounded causal truth**: structurally supported chain-backed explanations

## Runtime retrieval model
Preferred flow:
1. Agent decides memory is needed
2. Agent uses `memory.execute` as the canonical facade
3. Runtime may perform snapping, typed retrieval, and causal fallback internally
4. Response returns:
   - `results`
   - `chains`
   - `grounding`
   - `confidence`
   - `next_action`
   - `warnings`

## Dynamic source selection
- Same-session recent recall may benefit from transcript-first retrieval
- Cross-session durable recall should prefer Core Memory runtime surfaces
- Beads can point back to transcript turns, but are not guaranteed to be a verbatim transcript substitute

## Determinism principles
- stable tie-breaks
- append-only event logs where needed
- idempotent finalized-turn ingestion
- explicit grounding metadata
- replayable explain/debug artifacts
