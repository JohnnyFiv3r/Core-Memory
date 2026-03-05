# Structural Pipeline Contract

Canonical runtime contract for immutable causal edges:

1. Association rows (`index.associations`) are normalized with `core_memory/data/structural_relation_map.json`.
2. Normalized structural associations hydrate inline `bead.links` deterministically.
3. Structural links emit immutable structural edge events (`edge_add`, `class=structural`, `immutable=true`).
4. Graph materialization (`bead_graph.json`) is rebuilt from events.

Command:

```bash
core-memory --root <memory-root> graph sync-structural [--apply] [--strict]
```

- default: dry-run report only
- `--apply`: write links + edge events and rebuild graph
- `--strict`: fail non-zero on invariant violations

Invariant checks:
- `missing_link_from_association`
- `missing_graph_head_from_edge`

Structural relations are defined via `core_memory/data/structural_relation_map.json` and must map into the immutable causal set.
