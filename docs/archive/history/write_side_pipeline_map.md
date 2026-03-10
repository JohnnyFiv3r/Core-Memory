# Write-Side Pipeline Map

Status: Working Design Map
Audience: Maintainers / refactor planning
Purpose: capture the current end-to-end write-side memory construction pipeline before refactoring.

## Summary

Core Memory now has two major halves:
1. **Write-side memory construction pipeline**
2. **Read-side retrieval / reasoning pipeline**

This document covers the write-side only.

The write-side is not just “a pair of scripts.” It is the subsystem that turns conversation/session artifacts into durable structured memory, maintains rolling context, and preserves retrieval prerequisites.

## Core write-side responsibilities

The current write-side pipeline is responsible for:
- finalized-turn event ingestion
- transcript/session source discovery
- bead marker parsing
- bead validation and normalization
- bead persistence into the core store
- idempotent session extraction tracking
- session-level consolidation
- historical compaction targeting
- rolling/sliding context window generation
- artifact generation (`promoted-context.md`)
- downstream support for promotion, compaction, and retrieval quality

## Pipeline stages

### Stage 0 — Trigger surfaces

The write-side can be triggered from multiple places:

#### A. Finalized-turn native/integration path
Canonical port:
- `core_memory.integrations.api.emit_turn_finalized(...)`

Adapters that feed this path:
- OpenClaw native finalized-turn flow
- PydanticAI in-process adapter
- SpringAI HTTP ingress `/v1/memory/turn-finalized`

Characteristics:
- append-only event emission
- idempotency keyed by `session_id:turn_id`
- non-blocking write semantics expected by some adapters

#### B. Session-end / memoryFlush extraction path
Current operational entrypoints:
- `extract-beads.py`
- `consolidate.py`

These are still path-canonical operational scripts and are referenced directly by automation.

### Stage 1 — Source acquisition

Primary source types:
- OpenClaw session transcript JSONL under `/home/node/.openclaw/agents/<agent>/sessions/*.jsonl`
- store/session fallback transcript artifacts under `<CORE_MEMORY_ROOT>/.beads/...`

Current source resolution behavior in `extract-beads.py`:
- explicit session id if passed
- latest transcript fallback if no session id provided
- agent defaults to `main`

Invariant:
- extraction must be able to resolve a transcript source deterministically enough for automation use

### Stage 2 — Marker parsing

Current marker syntaxes supported:
1. HTML comment marker format
   - `<!--BEAD:{...}-->`
2. Attribute marker format
   - `{::bead type="..." title="..." /::}`

Current parser behavior:
- scans assistant message content only
- supports both simple and nested OpenClaw transcript message shapes
- extracts all bead markers from each assistant message

Invariant:
- backward compatibility for both marker syntaxes must be preserved unless explicitly deprecated later

### Stage 3 — Validation and normalization

Current validation behavior in `extract-beads.py`:
- checks bead type against `VALID_BEAD_TYPES`
- validates scope against `VALID_SCOPES`
- validates authority against `VALID_AUTHORITIES`
- title truncation before CLI write
- summary truncation before CLI write
- confidence coercion if parseable

Important note:
- current logic is a mix of parsing-time validation and write-time argument shaping
- this is a strong candidate for internal canonicalization during refactor

Invariant:
- invalid markers should fail locally/skipped without poisoning the whole extraction run

### Stage 4 — Persistence

Current persistence path for extraction:
- `extract-beads.py` shells out to:
  - `python -m core_memory.cli add ...`

This means the effective store write semantics are inherited from:
- `core_memory.cli`
- `core_memory.store`

Important consequence:
- write-side extraction currently depends on CLI boundary semantics, not just store-internal functions

Invariant:
- persisted beads must remain compatible with the core store’s canonical add path

### Stage 5 — Idempotency tracking

Current extraction idempotency marker:
- `<CORE_MEMORY_ROOT>/.beads/.extracted/session-<id>.json`

Stored payload includes:
- `session_id`
- `transcript`
- `written`
- `completed_at`

Behavior:
- if marker exists and `CORE_MEMORY_EXTRACT_ONCE != 0`, extraction is skipped

Invariant:
- session extraction must remain idempotent by session id unless explicitly disabled

### Stage 6 — Session-level consolidation

Current operational entrypoint:
- `consolidate.py consolidate --session <id> [--promote]`

Behavior:
1. compact one session
2. rebuild rolling window from current store state
3. compact historical excluded beads after window generation
4. write rolling window artifact

Important guardrail:
- safe default is no auto-promotion unless explicitly requested
- even with `--promote`, promotion remains gated by store policy / env flags

Invariant:
- consolidation must remain safe-by-default

### Stage 7 — Rolling/sliding window generation

Current operational entrypoint:
- `consolidate.py rolling-window`

Behavior:
- reads full bead set from store index
- excludes superseded beads
- sorts by pure recency (`promoted_at` or `created_at`)
- renders bounded textual window under token budget
- writes:
  - `promoted-context.md`

This artifact is effectively a write-side context-preparation output.

Invariant:
- rolling window ordering is recency-driven
- output path remains stable unless explicitly migrated later

### Stage 8 — Historical compaction targeting

Current consolidation behavior computes:
- included window bead ids
- excluded historical bead ids

Then compact logic is targeted so that:
- current window remains available
- historical beads can be compacted separately

This stage matters because it shapes downstream retrieval quality and memory density.

Invariant:
- currently included rolling-window content should not be compacted away in the same pass

### Stage 9 — Downstream artifacts / coupling

Current write-side artifacts and dependencies include:
- `.beads/.extracted/session-<id>.json`
- `promoted-context.md`
- current store index / event files

Known coupling points:
- `WORKFLOW_AUTO.md`
- OpenClaw memoryFlush prompt/config
- historical plans/docs
- operator expectations and path references

Implication:
- these paths are currently operational contracts, not just internal implementation details

## Current primary entrypoints

### Root operational scripts (path-canonical today)
- `extract-beads.py`
- `consolidate.py`

### Canonical internal/store dependencies already used
- `core_memory.cli`
- `core_memory.store.MemoryStore`
- event and finalized-turn ingestion layers

## Refactor direction (approved)

Approved direction:
- preserve root scripts as compatibility entrypoints
- internalize logic into a deeper write-side pipeline module graph
- do not move/rename root scripts in the first pass

Preferred target architecture:
- root scripts become thin orchestration wrappers
- real logic lives in canonical write-side internal modules

## Invariants to preserve during refactor

### Contract invariants
- root script filenames remain unchanged
- current CLI flag behavior remains unchanged
- current artifact paths remain unchanged
- current idempotency marker behavior remains unchanged

### Behavioral invariants
- transcript discovery behavior remains compatible
- both bead marker syntaxes remain supported
- skipped invalid markers remain non-fatal to the full run
- rolling-window artifact remains generated at the same path
- consolidation remains safe-by-default

### Integration invariants
- write-side pipeline remains compatible with OpenClaw automation references
- no immediate path churn for `WORKFLOW_AUTO.md`/memoryFlush consumers

## Candidate internal module graph for rewrite

Proposed internal structure (conceptual):
- `core_memory/write_pipeline/transcript_source.py`
- `core_memory/write_pipeline/marker_parse.py`
- `core_memory/write_pipeline/normalize.py`
- `core_memory/write_pipeline/persist.py`
- `core_memory/write_pipeline/idempotency.py`
- `core_memory/write_pipeline/consolidate.py`
- `core_memory/write_pipeline/window.py`
- `core_memory/write_pipeline/artifacts.py`
- `core_memory/write_pipeline/orchestrate.py`

This is intentionally pipeline-oriented rather than mirroring the old script boundaries 1:1.

## Why this map matters

The write-side pipeline used to be effectively the whole system. Even now, it remains critical because retrieval quality depends on what the write-side constructs and preserves.

Refactoring should therefore be approached as:
- a write-side subsystem internalization and hardening effort
- not as a script cleanup
- not as file-structure polish

## Immediate next safe step after this map

1. Freeze explicit contracts/invariants from this document
2. Add parity tests around current script behavior and artifacts
3. Begin internal module extraction behind wrapper-preserving root scripts
