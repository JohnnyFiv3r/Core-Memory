# Release Notes: core-graph-archive-retrieval

## Summary
This branch delivers the graph/archive retrieval stack (R1–R4) plus the full known-gap closure sequence through gap #7.

Base: `master@8c3f9f4`
Head: `core-graph-archive-retrieval@002818a`

## Included Commits (ordered)
- `e18bcaa` R1 archive index (O(1) hydration + rebuild CLI)
- `fc9de0b` R2 event log materializer + graph build/stats
- `bbbb981` R3 semantic index + traversal + semantic decay/reinforcement
- `6521748` R4 memory_reason API + reason CLI + docs
- `c0d55e4` Gap #1 provider embeddings backend (env-gated) + deterministic fallback
- `e394a33` Gap #2 semantic active-K enforcement + eviction/deactivation events
- `930265e` Gap #3 centrality-aware traversal + centrality stats
- `2ec4319` Gap #4 soft intent router with pathway fallback
- `aa285f7` Gap #5 chain dedupe/diversity + chain confidence scoring
- `484641a` Gap #6 structural inference hardening + infer-structural CLI
- `002818a` Gap #7 citation confidence enrichment + PR merge package

## New/Updated Interfaces
### CLI
- `metrics archive-index-rebuild`
- `graph build`
- `graph stats`
- `graph semantic-build`
- `graph semantic-lookup`
- `graph traverse`
- `graph decay`
- `graph infer-structural [--min-confidence N] [--apply]`
- `reason "<query>" --k N`

### Runtime Artifacts
- `memory/.beads/archive_index.json`
- `memory/.beads/events/edges.jsonl`
- `memory/.beads/bead_graph.json`
- `memory/.beads/bead_index_meta.json`
- `memory/.beads/bead_index.faiss` (optional)

## Behavior Guarantees
- Structural edges: immutable + append-only event provenance.
- Semantic edges: mutable via append-only events (reinforce/decay/deactivate).
- Retrieval remains local/inspectable with deterministic lexical fallback.
- `memory_reason` now includes:
  - route + route confidence
  - chain-level confidence
  - citation-level grounded role/confidence
  - overall confidence summary

## Validation Snapshot
- Unit/integration suite run: **44 tests passing**.
- Branch smoke query runs verified via `core-memory ... reason`.

## Recommended Post-Merge Smoke Commands
```bash
core-memory --root /home/node/.openclaw/workspace/memory metrics archive-index-rebuild
core-memory --root /home/node/.openclaw/workspace/memory graph semantic-build
core-memory --root /home/node/.openclaw/workspace/memory graph build
core-memory --root /home/node/.openclaw/workspace/memory graph stats
core-memory --root /home/node/.openclaw/workspace/memory reason "why did we decide candidate-only promotion?" --k 8
```
