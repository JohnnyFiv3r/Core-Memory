# Neo4j Quickstart (Shadow Graph)

Status: Optional adapter

## 1) Install optional dependency

```bash
pip install "core-memory[neo4j]"
```

Core Memory works without this extra; Neo4j commands will return explicit diagnostics when missing.

## 2) Configure Neo4j env vars

```bash
export CORE_MEMORY_NEO4J_ENABLED=1
export CORE_MEMORY_NEO4J_URI="bolt://localhost:7687"
export CORE_MEMORY_NEO4J_USER="neo4j"
export CORE_MEMORY_NEO4J_PASSWORD="<password>"
export CORE_MEMORY_NEO4J_DATABASE="neo4j"
# optional
export CORE_MEMORY_NEO4J_TLS=1
export CORE_MEMORY_NEO4J_TIMEOUT_MS=5000
```

## 3) Validate status

```bash
core-memory --root ./memory graph neo4j-status
```

Strict mode (non-zero on non-ready):

```bash
core-memory --root ./memory graph neo4j-status --strict
```

## 4) Run dry-run projection sync

```bash
core-memory --root ./memory graph neo4j-sync --dry-run --full
```

Session-scoped dry-run:

```bash
core-memory --root ./memory graph neo4j-sync --session-id s1 --dry-run
```

## 5) Execute sync

```bash
core-memory --root ./memory graph neo4j-sync --full
```

Session-scoped sync:

```bash
core-memory --root ./memory graph neo4j-sync --session-id s1
```

## 6) Optional prune

```bash
core-memory --root ./memory graph neo4j-sync --session-id s1 --prune
```

`--prune` removes shadow-graph entities outside the current sync scope in Neo4j. It does **not** mutate local Core Memory state.
Prune is constrained to rows marked with `cm_owner=core_memory_shadow_v1`.
