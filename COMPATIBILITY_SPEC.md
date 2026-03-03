# Compatibility Spec: Legacy `mem_beads` to `core_memory`

## Status
Finalized (post-migration).

## Public CLI Contract (current)
- Supported command: `core-memory`
- Removed: `mem-beads` alias

## Data Store Contract (current)
Primary root (recommended):
- `CORE_MEMORY_ROOT`

Compatibility env inputs accepted by migration/automation helpers:
- `MEMBEADS_ROOT`
- `MEMBEADS_DIR`

Store layout:
- `<root>/.beads/index.json`
- `<root>/.beads/global.jsonl`
- `<root>/.beads/session-<id>.jsonl`
- `<root>/.beads/archive.jsonl`
- `<root>/.beads/events/*.jsonl`
- `<root>/.turns/session-<id>.jsonl`

## Association Contract
- Associations are first-class records in `index.json`.
- Rebuild restores associations from `association_created` events.
- Per-add fast derived association pass is enabled by default and configurable via:
  - `CORE_MEMORY_ASSOCIATE_ON_ADD`
  - `CORE_MEMORY_ASSOCIATE_LOOKBACK`
  - `CORE_MEMORY_ASSOCIATE_TOP_K`

## Determinism / Safety Contract
- Archive-first bead durability under store lock.
- Atomic JSON writes for index and other JSON artifacts.
- Deterministic sorting for rewritten association sets.
- Rebuild is safe for both beads and associations.

## Migration Contract
- Legacy `.mem-beads` stores are migrated via explicit command:
  - `core-memory migrate-store --legacy-root <path>`
- Migration is idempotent and supports backup.
