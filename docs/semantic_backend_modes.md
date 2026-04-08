# Semantic Backend Modes

Status: Canonical guidance

Purpose: make production safety explicit for semantic retrieval deployments.

## Mode summary

| Backend mode | Intended use | Multi-worker write safety | Recommendation |
|---|---|---:|---|
| `lexical` (no semantic backend built) | base install / fallback | ✅ | acceptable for non-strict setups; not strict semantic guarantees |
| `faiss-*` local index | development, local single-process | ⚠️ single-process/single-writer only | use for dev or controlled single-worker deployments |
| `qdrant` | production semantic retrieval | ✅ | recommended production path |
| `pgvector` | production semantic retrieval | ✅ | recommended production path |

## Practical rule

- If you run a **single local worker** and want fast setup, FAISS/local can be fine.
- If you run **multi-worker** or shared production traffic, use a distributed-safe backend (`qdrant` or `pgvector`).

## Explicit backend selection (SEM-2)

Set backend mode explicitly:

```bash
export CORE_MEMORY_VECTOR_BACKEND=local-faiss   # default
# or
export CORE_MEMORY_VECTOR_BACKEND=qdrant
# or
export CORE_MEMORY_VECTOR_BACKEND=pgvector
# or
export CORE_MEMORY_VECTOR_BACKEND=chromadb
```

The retrieval layer routes through a backend interface and records the selected backend in semantic manifest metadata.

## Canonical semantic mode interaction

- `CORE_MEMORY_CANONICAL_SEMANTIC_MODE=required`
  - fails closed when semantic backend is unavailable.
- `CORE_MEMORY_CANONICAL_SEMANTIC_MODE=degraded_allowed`
  - allows lexical fallback with explicit degraded markers.

## Diagnostics

Use:

```bash
core-memory graph semantic-doctor
```

Look at:
- `backend`
- `deployment_profile`
- `multi_worker_safe`
- `concurrency_warning`
- `connectivity_checked`
- `connectivity_ok`
- `connectivity_error`
- `recommended_production_backends`

Concurrency hardening notes:
- semantic index builds use a build lock (`.beads/semantic/build.lock`) to avoid overlapping rebuild writes.
- if the lock is held, rebuild is treated as retryable and queued.
- stale locks are reclaimed automatically.
