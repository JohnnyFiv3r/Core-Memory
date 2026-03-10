# PR Merge Package: core-graph-archive-retrieval

## Scope
This branch delivers R1–R4 retrieval architecture plus known-gap closures through gap #7:

- R1 archive index (O(1) snapshot hydration)
- R2 event-sourced graph materializer
- R3 semantic lookup/traversal integration
- R4 `memory_reason` planner/answer path
- Gap #1 provider embeddings backend (env-gated, deterministic fallback)
- Gap #2 semantic active-K cache + deterministic eviction/deactivation
- Gap #3 centrality-aware traversal and centrality stats
- Gap #4 soft intent router with pathway fallback
- Gap #5 chain dedupe/diversity + chain confidence scoring
- Gap #6 strict structural inference hardening + safe CLI inference command
- Gap #7 citation enrichment + explicit confidence payload

## Key Behavior
- Structural edges remain immutable and auditable.
- Semantic edges remain mutable via append-only edge events.
- Retrieval remains local, deterministic, and inspectable.
- `memory_reason` now emits:
  - intent route + confidence
  - chain-level confidence
  - citation-level grounded-role + confidence
  - top-level overall confidence summary

## Risks
1. **Quality drift under sparse graph density**
   - Soft routing may select a path with weak evidence in low-data states.
   - Mitigation: confidence payload exposes weak grounding clearly.

2. **Embedding backend variability**
   - Provider-backed embeddings may alter ranking shape.
   - Mitigation: env-gated opt-in; deterministic fallback remains default-safe.

3. **Aggressive semantic edge eviction at low K**
   - Small `CORE_MEMORY_SEMANTIC_ACTIVE_K` may drop useful edges.
   - Mitigation: deterministic ranking + event trail + tunable K.

## Rollout Notes
1. Merge branch into integration environment.
2. Rebuild indexes:
   - `core-memory --root <memory-root> metrics archive-index-rebuild`
   - `core-memory --root <memory-root> graph semantic-build`
   - `core-memory --root <memory-root> graph build`
3. Run smoke checks:
   - `core-memory --root <memory-root> graph stats`
   - `core-memory --root <memory-root> reason "why did we decide X?" --k 8`
4. Optional strict inference preview:
   - `core-memory --root <memory-root> graph infer-structural --min-confidence 0.9`
   - apply only after review: `--apply`

## Validation
- Current local suite: 44 tests passing (including new R4 router/diversity/confidence + structural hardening tests).
