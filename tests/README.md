# Tests — Subsystem Ownership Map

This directory is intentionally flat (~410 `test_*.py` files). Rather than
reorganize into packages (which churns imports and history), this map routes the
filename **prefix** to the subsystem it exercises, following the architecture
layering (`schema → persistence → domain → retrieval → runtime → integrations`,
see `docs/architecture_overview.md`). Use it to find the tests for a change, and
to run a focused subset with `-k`.

> Move tests into packages only during a focused subsystem cleanup — not as a
> standalone reorg (per the cleanup plan's A14 guidance).

## Prefix → subsystem

| Prefix (`tests/test_<prefix>_*.py`) | Subsystem | Code under test |
|---|---|---|
| `schema`, `models`, `normalization` | Schema | `core_memory/schema/` — bead/relation/claim vocabulary, models |
| `store`, `persistence`, `bead`, `backend`, `sqlite`, `rebuild`, `edge`, `supersession`, `promotion` | Persistence | `core_memory/persistence/` — MemoryStore, backends, promotion service, store ops |
| `claim` | Claims | `core_memory/claim/` — claim extraction / current-truth resolution |
| `entity` | Entity registry | `core_memory/entity/` |
| `association`, `graph`, `worldline`, `storyline`, `root_cause`, `edge_weights`, `traversal` | Domain graph | `core_memory/association/`, `core_memory/graph/` |
| `retrieval`, `recall`, `causal_recall`, `effort`, `search`, `lexical`, `pipeline` | Retrieval | `core_memory/retrieval/` |
| `runtime`, `turn`, `session`, `flush`, `rolling`, `side_effect`, `queue`, `jobs`, `engine` | Runtime | `core_memory/runtime/` (turn/flush/session/passes/queue) |
| `dreamer`, `assembly_depth`, `geometry`, `convergence`, `tension`, `projection`, `goal_discovery`, `goal_decay` | Dreamer | `core_memory/runtime/dreamer/` |
| `myelination` | Myelination | `core_memory/runtime/observability/myelination*` |
| `soul`, `goal_lifecycle`, `identity_value` | SOUL + goal lifecycle | `core_memory/soul/`, `core_memory/persistence/goal_lifecycle_v2.py` |
| `semantic`, `calibration` | Semantic-task runtime | `core_memory/policy/semantic_task_runtime.py` + `semantic_task_verifier.py`, `core_memory/schema/semantic_tasks.py` |
| `http`, `ingress`, `contract` | HTTP integration | `core_memory/integrations/http/` |
| `mcp`, `sidecar`, `protocol` | MCP integration | `core_memory/integrations/mcp/` |
| `openclaw`, `pydanticai`, `neo4j`, `graphiti`, `obsidian`, `springai`, `langchain`, `crewai` | Framework/graph adapters | `core_memory/integrations/*` |
| `cli`, `metrics`, `doctor`, `init` | CLI | `core_memory/cli/` |
| `ingest`, `external_evidence`, `external_versioning`, `source`, `recording`, `transcript` | Typed ingest + provenance | `core_memory/runtime/ingest/`, transcript ingest |
| `memory`, `management`, `maintain`, `governance` | Public API + maintenance facade | `core_memory/__init__.py`, `core_memory/memory.py`, `core_memory/management.py` |
| `architecture`, `*_guard`, `*_audit`, `layering`, `flat_file` | Architecture guardrails | `scripts/check_architecture_guards.py` + baselines |
| `e2e`, `demo` | End-to-end / scenarios | full write→recall paths |

## Running a subset

```bash
# One subsystem
python -m pytest tests/ -k "soul or goal_lifecycle" -q

# Guardrails only (fast, CI-equivalent)
python -m pytest tests/test_architecture_guards.py -q

# Everything (semantic autodrain off keeps it hermetic)
CORE_MEMORY_SEMANTIC_AUTODRAIN=off python -m pytest tests/ -q
```

Prefixes not listed here map to the nearest subsystem by name; when adding a new
test, keep the existing `test_<subsystem>_<behavior>.py` convention so this map
stays discoverable.
