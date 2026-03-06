# PydanticAI Integration Guide

Status: Canonical
Canonical surfaces:
- `run_with_memory(...)`
- `emit_turn_finalized(...)`
- `memory.execute`

## Architecture
PydanticAI is the simplest non-OpenClaw integration because it runs in Python and can call Core Memory directly without an HTTP bridge.

Two paths exist:
1. **Write path** — emit finalized-turn memory events
2. **Runtime path** — use memory skill/tool functions in-process

## Write path
Canonical write path:
- `core_memory.integrations.api.emit_turn_finalized(...)`

Convenience helper:
- `core_memory.integrations.pydanticai.run_with_memory`

## Runtime path
Preferred unified runtime surface:
- `core_memory.tools.memory.execute`

Optional direct surfaces:
- `memory.search`
- `memory.reason`
- `memory.get_search_form`

## Why this path is attractive
- no HTTP bridge required
- no separate auth/token concerns for runtime calls
- direct Python function access
- lower integration overhead than SpringAI

## Recommended usage model
- use `run_with_memory(...)` or equivalent finalized-turn emission for writes
- use `memory.execute` as the main runtime retrieval/reasoning facade
- use lower-level memory operations only when you need specialized control
