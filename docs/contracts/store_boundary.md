# Store Boundary Contract

Status: Locked
Date: 2026-03-14

## Purpose
Define strict layering so persistence does not own turn-time intelligence.

## Rules

`core_memory.store` / `core_memory.persistence.store` MUST NOT own:
- per-turn orchestration
- promotion decision policy
- association inference policy
- lifecycle trigger routing

`store` MAY own:
- bead/association persistence primitives
- projection/index maintenance
- archive pointer persistence
- metrics state persistence

## Layer ownership
- Runtime orchestration: `core_memory.runtime.*`
- Promotion policy: `core_memory.policy.promotion`
- Association policy: `core_memory.association.*`
- Persistence: `core_memory.store` (transitioning to `core_memory.persistence.store`)

## PR gate
Any PR introducing new decision heuristics into store should be rejected.
