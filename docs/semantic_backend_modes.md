# Semantic Backend Modes

Status: Canonical guidance

Purpose: make production safety explicit for semantic retrieval deployments.

## Mode summary

| Backend mode | Intended use | Multi-worker write safety | Recommendation |
|---|---|---:|---|
| `lexical` (no semantic backend built) | base install / fallback | ✅ | acceptable for non-strict setups; not strict semantic guarantees |
| `qdrant` | production semantic retrieval | ✅ | **default**; recommended production path |
| `pgvector` | production semantic retrieval | ✅ | recommended production path |
| `faiss-*` local index | legacy local single-process | ⚠️ single-process/single-writer only | **deprecated** — emits a warning, scheduled for removal in the next major version |

## Practical rule

- The default (`qdrant`) is embedded/zero-ops and safe for both local and
  multi-worker deployments.
- FAISS remains available behind an explicit opt-in for existing local
  setups, but is deprecated.

## Explicit backend selection (SEM-2)

Set backend mode explicitly:

```bash
export CORE_MEMORY_VECTOR_BACKEND=qdrant        # default
# or
export CORE_MEMORY_VECTOR_BACKEND=pgvector
# or
export CORE_MEMORY_VECTOR_BACKEND=chromadb
# or
export CORE_MEMORY_VECTOR_BACKEND=local-faiss   # deprecated opt-back
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

## Benchmark run guidance (PH-2)

For benchmark runs, make backend mode explicit in command/config and record it in report metadata.

Recommended benchmark invocations:

```bash
# Safe default for local benchmark smoke (honest degraded fallback allowed)
python -m benchmarks.locomo_like.runner --subset local \
  --semantic-mode degraded_allowed \
  --vector-backend local-faiss

# Strict semantic backend requirement (fails closed if backend unusable)
python -m benchmarks.locomo_like.runner --subset local \
  --semantic-mode required \
  --vector-backend qdrant
```

Interpretation:
- `degraded_lexical`: no usable semantic backend; lexical fallback active.
- `local_single_writer`: local FAISS/Chroma usable but single-writer profile.
- `external_distributed`: distributed-safe backend profile (qdrant/pgvector) usable.
- `strict_missing_backend`: required mode requested without usable semantic backend.
