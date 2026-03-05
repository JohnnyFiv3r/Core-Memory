# Concept Checkpoint — Memory Skill Elegance

Date: 2026-03-05 (UTC)
Branch: memory-skill-typed-search-ab

## User intuition captured
"Search does not yet feel as elegant as bead writing."

## ChatGPT proposal (evaluated)
- Introduce one canonical primitive for retrieval requests (`MemoryRequest`)
- Use one execution interface (`memory.execute(request) -> MemoryResponse`)
- Keep search + reason engines internal
- Always return candidates (if corpus non-empty), even for causal asks
- Treat grounding as explicit status (`achieved`/`reason`) instead of hard-empty result

## My assessment
Strongly agree with core direction.

### What is correct
1. **Canonical request artifact** is the missing elegance layer.
2. **Single response shape** improves predictability and UX.
3. **Grounding as status** is better than mode-driven empties.
4. **Pipeline composition** (retrieve first, then proof/chain) is aligned with deterministic design.

### What to keep from current architecture
- Keep two internal engines:
  - typed retrieval engine
  - causal reasoning/chain engine
- Keep explicit operations in tool family (`memory.search`, `memory.reason`) for testability.
- Add a unifying orchestrator contract on top (`memory.execute`) rather than replacing internal ops.

## Design decision checkpoint
Adopt a layered model:

1) **Public canonical primitive**
- `MemoryRequest` (typed)
- `MemoryResponse` (typed)

2) **Execution contract**
- `memory.execute(request)` (single skill-facing entry)
- Internally routes/stages to search and/or reason deterministically

3) **Never-empty contract**
- If corpus has data, `results` should be non-empty whenever feasible
- For ungrounded causal asks, return context results +
  - `grounding.required=true`
  - `grounding.achieved=false`
  - `grounding.reason=<explicit>`

## Next implementation slice
- Add `memory.execute` wrapper over existing typed search/reason engines.
- Introduce formal request/response schema files and tests.
- Implement deterministic fallback ladder to prevent empty causal outputs when anchors exist.
