# Neo4j Integration Guide (Shadow Graph Adapter)

Status: Optional adapter

## Purpose
Neo4j integration is a one-way projection from Core Memory into a graph database for:
- visualization
- debugging causal shape
- reviewer/demo workflows
- offline Cypher exploration

It is not a canonical runtime dependency.

## Authority model
- Core Memory local store (`.beads/index.json` + canonical runtime surfaces) is authoritative.
- Neo4j receives mirrored projection data.
- Neo4j failures must not block canonical Core Memory behavior.

## What this adapter does
- maps beads -> Neo4j nodes
- maps associations -> Neo4j relationships
- supports idempotent upserts
- supports optional scoped prune

## What this adapter does not do
- no canonical write ownership
- no canonical retrieval ownership
- no replacement of in-repo traversal or memory request engine

## Data projection model

### Nodes
- base label: `Bead`
- type label: e.g. `Decision`, `Lesson`, `Outcome`, `SessionStart`
- key: `bead_id`

### Relationships
- relationship type: `ASSOCIATED`
- key: `association_id` (fallback stable dedupe key if source lacks id)
- semantic relationship preserved in property `relationship` (e.g. `supports`, `contradicts`)

## Sync modes
- full sync (`--full`)
- filtered sync (`--session-id`, `--bead-id`)
- dry-run (`--dry-run`)
- optional prune (`--prune`) scoped to selected sync scope

### Prune safety boundary
Prune only targets shadow-projected rows owned by Core Memory (`cm_owner=core_memory_shadow_v1`) and the active projection dataset key (`cm_dataset`).
It does not prune unrelated `:Bead` / `:ASSOCIATED` data that lacks this ownership marker.

## Failure isolation guarantees
- Neo4j dependency/config/connection errors return explicit diagnostic payloads
- local Core Memory data is not mutated by Neo4j sync operations
- canonical runtime (`process_turn_finalized`, `process_session_start`, `process_flush`, `search/trace/execute`) remains independent

## Cypher recipes

### 1) Latest decision -> outcome -> lesson chains
```cypher
MATCH (d:Bead {type:'decision'})-[r1:ASSOCIATED]->(o:Bead {type:'outcome'})
MATCH (o)-[r2:ASSOCIATED]->(l:Bead {type:'lesson'})
RETURN d.title AS decision, o.title AS outcome, l.title AS lesson,
       r1.relationship AS d_to_o, r2.relationship AS o_to_l,
       d.created_at AS decided_at
ORDER BY decided_at DESC
LIMIT 25;
```

### 2) Superseded decisions
```cypher
MATCH (d1:Bead {type:'decision'})-[r:ASSOCIATED]->(d2:Bead {type:'decision'})
WHERE r.relationship IN ['supersedes', 'superseded_by']
RETURN d1.title, r.relationship, d2.title, r.created_at
ORDER BY r.created_at DESC
LIMIT 50;
```

### 3) Contradiction links
```cypher
MATCH (a:Bead)-[r:ASSOCIATED]->(b:Bead)
WHERE r.relationship = 'contradicts'
RETURN a.title AS left_bead, b.title AS right_bead, r.reason_text, r.confidence
ORDER BY r.confidence DESC, r.created_at DESC
LIMIT 50;
```

### 4) Session-start snapshots
```cypher
MATCH (s:SessionStart)
RETURN s.session_id, s.title, s.created_at, s.summary
ORDER BY s.created_at DESC
LIMIT 20;
```

### 5) Orphan/sparse-link debugging
```cypher
MATCH (b:Bead)
OPTIONAL MATCH (b)-[r:ASSOCIATED]-()
WITH b, count(r) AS degree
WHERE degree = 0
RETURN b.bead_id, b.type, b.title, b.session_id, b.created_at
ORDER BY b.created_at DESC
LIMIT 100;
```
