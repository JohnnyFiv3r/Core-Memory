---
name: core-memory
summary: Use Core Memory canonical write/read/flush surfaces from OpenClaw integrations without bypassing runtime authority.
description: Use when working with Core Memory from OpenClaw, including finalized-turn writes, memory retrieval, bridge behavior, semantic bead fields, promotion, claims, associations, or integration debugging.
---

# Core Memory OpenClaw Integration

Before changing Core Memory/OpenClaw integration behavior, read the authoritative companion instructions:

`../../../../docs/integrations/openclaw/core-memory-skill-instructions.md`

Follow those instructions unless they conflict with higher-priority system/developer instructions or canonical Core Memory docs.

Key reminders:
- OpenClaw is not the memory authority; Core Memory is.
- Route finalized-turn writes through canonical ingestion (`process_turn_finalized(...)` / bridge event emission).
- Route retrieval through canonical read surfaces.
- Do not mutate `.beads`, `.turns`, archive, flush, or rolling-window files directly.
- Keep the OpenClaw bridge thin; do not move memory-engine semantics into plugin JS.
- Questions are retrieval/context turns, not declarative memories to promote.
- Treat `because` as grounded free-text support for applied semantic labels/state; short user-text quotes are valid support when grounded, but do not use guessed filler or long whole-turn dumps.
- Use canonical association relationship types, not helper labels as durable relations.
