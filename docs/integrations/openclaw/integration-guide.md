# OpenClaw Integration Guide

Status: Canonical
Canonical surfaces:
- `emit_turn_finalized(...)`
- `memory.execute`
- `memory.search`
- `memory.reason`

## Architecture
OpenClaw is the native/original environment where Core Memory runs in-process with the main agent runtime.

Core pieces:
1. finalized-turn write-path ingestion
2. memory sidecar / processing pipeline
3. runtime memory skill surface
4. eval/validation harnesses

## Write path
Canonical write port:
- `core_memory.integrations.api.emit_turn_finalized(...)`

OpenClaw’s finalized-turn handling should converge here so exactly one deterministic memory event is emitted per top-level user turn.

## Runtime path
Canonical runtime surface:
- `core_memory.tools.memory.execute`

OpenClaw can also access lower-level runtime operations:
- `memory.search`
- `memory.reason`
- `memory.get_search_form`

## Source hierarchy
OpenClaw is uniquely positioned to access:
- transcript/recent session context
- structured memory graph
- archived memory artifacts

Policy guideline:
- same-session recent recall may use transcript-first
- durable/cross-session memory should prefer Core Memory surfaces

## Config and models
The OpenClaw runtime selects models and allowlists through OpenClaw config, not Core Memory itself. Core Memory relies on those runtime selections for agent execution and reasoning behavior.

## Current docs to consult
- `../../canonical_surfaces.md`
- `../../core_adapters_architecture.md`
- `../../memory_search_skill.md`
- `../../memory_search_agent_playbook.md`
