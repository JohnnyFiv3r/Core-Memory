# Legacy Path Exposure Log

Date opened: 2026-03-12
Owner: Core Memory implementation stream
Branch: feat/pr01-scaffolding-bootstrap

Purpose: Track every discovered legacy/compatibility path touched during implementation so we can close them safely with full auditability.

---

## Logging format

For each entry, capture:
- ID
- Area (`turn_path`, `flush_path`, `retrieval`, `integration`, `store`)
- File/module
- Legacy path description
- Exposure type (`active`, `shim`, `compat import`, `deprecated callable`, `docs mismatch`)
- Risk (`low`, `medium`, `high`)
- Safe closure plan
- Dependency blockers
- Status (`open`, `in_progress`, `closed`)
- PR / commit references

---

## Entries

### LP-001
- Area: integration
- File/module: `core_memory/openclaw_integration.py`
- Legacy path description: Deprecated wrapper remains import-visible and can be used instead of canonical engine path.
- Exposure type: shim
- Risk: medium
- Safe closure plan: Keep shim during transition; add hard warning + tests enforcing canonical entrypoints.
- Dependency blockers: Integration parity tests must pass without direct shim dependence.
- Status: open
- PR/commit refs: pending

### LP-002
- Area: turn_path
- File/module: `core_memory/trigger_orchestrator.py`
- Legacy path description: Legacy trigger dispatch path can be mistaken for canonical runtime.
- Exposure type: deprecated callable
- Risk: high
- Safe closure plan: fence usage; route all turn-finalized writes through `memory_engine.process_turn_finalized`.
- Dependency blockers: existing tests referencing orchestrator behavior.
- Status: in_progress
- PR/commit refs: feat/pr01-scaffolding-bootstrap (turn decision pass enforced in memory_engine; trigger_orchestrator shim usage logged to legacy-shim-usage.jsonl; strict block mode via CORE_MEMORY_BLOCK_LEGACY_TRIGGER_ORCHESTRATOR=1; readiness summarized by `core-memory metrics legacy-readiness`)

### LP-003
- Area: flush_path
- File/module: `core_memory/write_triggers.py`
- Legacy path description: historical write trigger behavior overlaps with canonical flush ownership.
- Exposure type: deprecated callable
- Risk: high
- Safe closure plan: enforce `process_flush` as sole flush authority; keep adapter wrappers until tests migrate.
- Dependency blockers: trigger/flush regression suite.
- Status: in_progress
- PR/commit refs: feat/pr01-scaffolding-bootstrap (association append dedupe + turn-path hardening landed; flush once-per-cycle guard + explicit flush phase trace + flush report artifact logging added; write_triggers fenced behind opt-in legacy env + delegated to canonical owners)

### LP-004
- Area: rendering
- File/module: `core_memory/rolling_surface.py`
- Legacy path description: operator-facing derived artifact can be conflated with authority source.
- Exposure type: docs mismatch
- Risk: medium
- Safe closure plan: maintain strict docs/tests that rolling surface is derived-only.
- Dependency blockers: none.
- Status: open
- PR/commit refs: pending

### LP-005
- Area: store
- File/module: `core_memory/store.py`
- Legacy path description: mixed old/new policy branches (including compact-time auto-promote flags) create ambiguity.
- Exposure type: active
- Risk: high
- Safe closure plan: isolate promotion policy authority and remove conflicting branches after compatibility window.
- Dependency blockers: schema compatibility + promotion regression tests.
- Status: in_progress
- PR/commit refs: feat/pr01-scaffolding-bootstrap (promotion lock invariant + decision-state normalization)

---

## Change control notes
- Update this log in every implementation PR.
- No legacy path removal without: test coverage, migration note, and rollback path.
- Promotion monotonicity (`promoted` terminal) is treated as a release-blocking invariant.
