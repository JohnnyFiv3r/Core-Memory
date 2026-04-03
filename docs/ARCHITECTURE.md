# Core Memory Architecture

## Five Canonical Centers

The codebase revolves around five canonical centers:

### 1. Write Ingress / Runtime Orchestration
**Files:** `event_ingress.py`, `event_state.py`, `event_worker.py`, `memory_engine.py`

Canonical finalized-turn event emission, pass state, mechanical execution, and runtime orchestration.

### 2. Live/Session/Rolling Surfaces
**Files:** `session_surface.py`, `live_session.py`, `rolling_record_store.py`, `write_pipeline/*`

- `session_surface.py` - live append authority for session JSONL
- `live_session.py` - read resolver with index projection fallback
- **`rolling_record_store.py`** - **canonical rolling continuity authority (JSON)**
- `rolling_surface.py` - renderer + selection policy (derived artifacts)

### 3. Retrieval / Planning
**Files:** `retrieval/pipeline/canonical.py`, `retrieval/semantic_index.py`, `retrieval/visible_corpus.py`, `retrieval/normalize.py`, `retrieval/query_norm.py`

- `retrieval/pipeline/canonical.py` - single canonical planner authority (`search`/`trace`/`execute`)
- `retrieval/semantic_index.py` - semantic lookup backend + stale-serving/rebuild lifecycle
- `retrieval/visible_corpus.py` - canonical visible corpus assembly
- `retrieval/normalize.py` - shared normalization/token handling
- `retrieval/query_norm.py` - query intent classification helpers

### 4. Graph / Causal Memory
**Files:** `graph.py`, `graph_structural.py`, `graph_traversal.py`, `graph_semantic.py`

- `graph_structural.py` - sync, backfill, inference
- `graph_traversal.py` - causal traversal and queries
- `graph_semantic.py` - reinforcement, decay, deactivation

### 5. Persistence Primitives
**Files:** `store.py`, `archive_index.py`, `io_utils.py`

- **`store.py`** - slim persistence façade (bead CRUD, index management)
  - Legacy compatibility methods remain as thin delegators to extracted modules
- `archive_index.py` - O(1) archive snapshot lookup
- `io_utils.py` - lock, atomic write, JSONL append

---

## Status Markers

| File | Status | Notes |
|------|--------|-------|
| `trigger_orchestrator.py` | DEPRECATED | Compatibility shim; use `memory_engine.py` |
| `openclaw_integration.py` | DEPRECATED | Use `integrations/openclaw_agent_end_bridge.py` |
| `association/preview.py` | ACTIVE (non-authoritative) | Secondary deterministic preview path |
| `integrations/springai/bridge.py` | ACTIVE/BRIDGE | Keep if used |
| `rolling_surface.py` | DEPRECATED | Renderer only; use `rolling_record_store.py` |
| `write_triggers.py` | DEPRECATED | Refactored to direct calls; subprocess removed |

---

## Store Extraction Status

Extraction is **largely complete**. Legacy compatibility methods remain in `store.py` as thin delegators to:

- `retrieval/query_norm.py` - tokenization, intent detection
- `hygiene.py` - redaction, sanitization
- `policy/promotion.py` - scoring, thresholds

---

## File Organization Guidelines

- **Keep retrieval logic in `retrieval/`**
- **Keep hygiene/content in `hygiene.py`**
- **Keep graph ops in `graph*.py` or `graph/` subpackage**
- **Store should be slim: persistence only, not inference/ranking**
