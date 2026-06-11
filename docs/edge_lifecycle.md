# Edge Lifecycle — reinforcement, decay, supersession

Status: Canonical

Associations have a usage lifecycle in addition to their status lifecycle
(`retract` / `supersede` / `reaffirm`, see
`specs/agent-authored-turn-memory-v1.md` slice 5). The usage lifecycle is the
"maintain" loop for causal edges: edges that keep contributing to recall rank
higher; edges that stop contributing decay toward a floor; edges through
superseded beads are penalised. Decay demotes — it never deletes.

## Flow: log on read, apply on write, consume on traverse

1. **Record** — `recall()` appends the edges that contributed to a delivered
   answer to `.beads/events/edge-usage.jsonl` (fire-and-forget; the read path
   never mutates the index). An edge "contributed" when both endpoints appear
   in the delivered evidence, or it lies on a causal path selected by the
   attribution pipeline and matches a real association in either orientation.
   Read-time pseudo-edges (because/evidence materialisation) are never
   reinforced.
2. **Fold** — at session flush (`process_flush`), usage is aggregated under
   the store lock into the association rows: `reinforcement_count += n`,
   `last_reinforced_at = now`. The log truncates only after the index write
   succeeds, so a crash replays usage instead of losing it. Fold stats are
   reported on the flush result as `edge_lifecycle`.
3. **Score** — traversal multiplies each edge score by
   `effective_edge_multiplier(assoc)` from `graph/edge_weights.py`.

## Scoring

```
multiplier = reinforcement_bonus × recency_decay        # range [0.70, 1.15]
reinforcement_bonus = 1 + min(0.15, 0.05 · ln(1 + reinforcement_count))
recency_decay = clamp(2^(-age_days / 90), 0.70, 1.0)
```

- Age is measured from `last_reinforced_at`, falling back to `created_at`;
  edges with no timestamp do not decay. Reinforcement refreshes the clock.
- The bonus is logarithmic and capped at +15% so usage tunes ranking without
  letting popularity swamp relevance or provenance.
- The decay floor (0.70) keeps old-but-valid edges retrievable — they stop
  outranking recently-confirmed structure, they never disappear.
- Edges with a superseded endpoint bead are additionally multiplied by
  `SUPERSEDED_ENDPOINT_FACTOR = 0.60`.

## Fields (assigned by the fold, never by the model payload)

| Field | Meaning |
|---|---|
| `reinforcement_count` | cumulative contributed-to-recall count (both orientations fold onto the same row) |
| `last_reinforced_at` | ISO timestamp of the most recent fold that touched this edge |

## Source modules

- `core_memory/association/edge_lifecycle.py` — record / collect / fold
- `core_memory/graph/edge_weights.py` — multiplier and constants
- `core_memory/runtime/flush/flush_flow.py` — fold call at the flush boundary
