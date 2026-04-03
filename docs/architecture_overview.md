# Architecture Overview

Status: Canonical

Core Memory is an event/session-first memory system that separates write ingestion, retrieval, association inference, and hydration.

## 1) Write path
Primary ingress:
- `core_memory.integrations.api.emit_turn_finalized(...)`

Responsibilities:
- finalized-turn ingest
- idempotent event append
- session/bead state updates
- continuity refresh

## 2) Retrieval path (canonical)
Primary runtime family:
- `search`
- `trace`
- `execute`

Planner authority:
- `core_memory/retrieval/pipeline/canonical.py`

## 3) Association inference
Association inference contract is defined by v2.1 policy (strict model-inferred write validation + quarantine for malformed/non-canonical rows).

This is a write-quality hardening layer, not a historical data migration layer.

## 4) Hydration
Hydration is explicit source/turn recovery after retrieval selection.

Canonical public hydration contract:
- `turn_sources`: `cited_turns` | `cited_turns_plus_adjacent`
- `max_beads`
- `adjacent_before`
- `adjacent_after`

Deep recall remains separate from this hydration contract.

## 5) Adapters
Current adapters:
- OpenClaw (native bridge-driven environment)
- PydanticAI (in-process)
- SpringAI / HTTP (service bridge)
- LangChain (`CoreMemory`, `CoreMemoryRetriever`)

## Tenant-aware HTTP behavior
Stateful HTTP memory endpoints support tenant scoping with `X-Tenant-Id`.
Reads/writes/flush/continuity operate on the same tenant-specific subtree when provided.

## Non-goal
Transcript/index dump replay is not the primary write architecture.
Transcript artifacts are bridge/feed inputs, not canonical write authority by themselves.
