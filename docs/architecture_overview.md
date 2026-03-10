# Architecture Overview

Status: Canonical

Core Memory is an event/session-first memory system.

## Runtime core
- `core_memory.memory_engine` — canonical runtime sequencing owner
- `core_memory.integrations.api.emit_turn_finalized(...)` — canonical write ingress

## Primary data surfaces
- Live session authority: `.beads/session-<id>.jsonl`
- Archive/projection surface: `.beads/index.json` (projection/cache, not live authority)
- Continuity authority: `rolling-window.records.json`

## Lifecycle in one line
`agent_end/finalized-turn -> emit_turn_finalized -> memory_engine processing -> session/archive/continuity updates -> retrieval via typed surfaces`

## Non-goal
Transcript/index-dump is not a supported primary write architecture.
