# Write-Side Event Model Gap Baseline (Phase 1)

Status: Canonical planning artifact
Purpose: Capture current trigger authority vs target event-native write-side orchestration.

## Current state (baseline)

Core Memory currently has event emission and event-processing paths, but write-side orchestration is still mixed:
- events are emitted in modern sidecar/integration flows
- root script and maintenance flows still execute critical write-side behavior directly
- some event records are observational/byproduct outputs rather than authoritative trigger boundaries

## Observed trigger classes

### A) Event-emitting integration triggers
- finalized-turn integration port emits memory events
- adapters route through canonical ingress surfaces

### B) Operational/script triggers
- `extract-beads.py` can trigger extraction and persistence behavior directly
- `consolidate.py` can trigger compaction/window behavior directly
- automation/memoryFlush pathways may invoke these scripts by path

### C) Mixed authority side effects
- event artifacts/logs exist, but not every critical write-side action is currently initiated by an authoritative event orchestration boundary

## Target state

Write-side should become **event-native**:
- events define the canonical trigger boundary for write-side processing
- script paths become compatibility wrappers over event-aware/canonical internals
- event emission is no longer merely a byproduct where orchestration authority is required

## Gap statement

Current gap:
- event-observable write-side with mixed trigger authority

Target:
- event-native write-side with clear trigger authority and wrapper compatibility

## Phase implication

Before write-side internalization is complete, refactor planning must explicitly handle:
1. which operations become event-authoritative
2. how compatibility scripts delegate into event-aware internals
3. how idempotency and ordering are preserved across both trigger classes during transition

## Non-goals in this baseline document
- no runtime behavior changes
- no trigger rewiring in this phase
- no endpoint/interface changes

This is a baseline reference for later trigger-model correction phases.
