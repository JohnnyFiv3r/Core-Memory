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
- `rolling_record_store.py` - canonical rolling continuity authority (JSON)
- `rolling_surface.py` - renderer + selection policy (derived artifacts)

### 3. Retrieval / Reasoning
**Files:** `retrieval/*`, `tools/memory_reason.py`, `memory_skill/*`

- `retrieval/query_norm.py` - query normalization, tokenization, intent detection
- `retrieval/lexical.py` - deterministic lexical scoring
- `retrieval/hybrid.py` - semantic + lexical fusion
- `retrieval/rerank.py` - second-stage reranking
- `retrieval/quality_gate.py` - retry/no-retry quality gating
- `retrieval/context_recall.py` - strict→fallback matching with deep recall
- `retrieval/failure_patterns.py` - failure signature detection
- `tools/memory_reason.py` - freeform reasoning (why/when/changed/remember)
- `memory_skill/*` - typed search catalog, snap, execute

### 4. Graph / Causal Memory
**Files:** `graph.py`, `graph_structural.py`, `graph_traversal.py`, `graph_semantic.py`

- `graph_structural.py` - sync, backfill, inference
- `graph_traversal.py` - causal traversal and queries
- `graph_semantic.py` - reinforcement, decay, deactivation

### 5. Persistence Primitives
**Files:** `store.py`, `archive_index.py`, `io_utils.py`

- `store.py` - slim persistence façade (bead CRUD, index management)
- `archive_index.py` - O(1) archive snapshot lookup
- `io_utils.py` - lock, atomic write, JSONL append

---

## Status Markers

| File | Status | Notes |
|------|--------|-------|
| `trigger_orchestrator.py` | DEPRECATED | Compatibility shim; use `memory_engine.py` |
| `openclaw_integration.py` | DEPRECATED | Use `integrations/openclaw_agent_end_bridge.py` |
| `association/pass_engine.py` | LEGACY | Secondary deterministic path |
| `integrations/springai/bridge.py` | ACTIVE/BRIDGE | Keep if used |

---

## File Organization Guidelines

- **Keep retrieval logic in `retrieval/`**
- **Keep hygiene/content in `hygiene.py`**
- **Keep graph ops in `graph*.py` or `graph/` subpackage**
- **Store should be slim: persistence only, not inference/ranking**
