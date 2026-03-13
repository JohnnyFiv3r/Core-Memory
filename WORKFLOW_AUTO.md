# WORKFLOW_AUTO.md (Canonical Automation Instructions)

Status: **Canonical**
Date: 2026-03-13

Purpose: Single root-level automation contract for agent/heartbeat/session-end flows.

---

## 0) Core rule
Always follow canonical runtime ownership:
- Turn path authority: `memory_engine.process_turn_finalized`
- Flush path authority: `memory_engine.process_flush`

Never route primary automation through deprecated shim paths.

---

## 1) On every turn-finalized / `agent_end`

Required actions:
1. Emit finalized turn into canonical ingestion.
2. Ensure one current-turn bead is written.
3. Append relevant in-session associations.
4. Run promotion-state decision pass across visible session beads.

Required invariants:
- Idempotent by `(session_id, turn_id)`.
- Promotion monotonicity: once `promoted`, never demote/unpromote.
- No compaction on turn path.

---

## 2) On memory flush cycle only

Required sequence:
1. Archive compaction (session scope)
2. Rolling-window maintenance write
3. Archive compaction (historical scope)
4. Commit flush checkpoint and report artifact

Required invariants:
- Flush runs once per latest processed turn/cycle (duplicate flush must skip).
- Flush report stages must include committed/skipped/failed as applicable.

---

## 3) Health checks during automation windows

Run and enforce:
1. `core-memory metrics canonical-health`
   - must report `all_green=true`
2. `core-memory metrics legacy-readiness`
   - monitor shim/legacy usage counts

Optional artifact write:
- `core-memory metrics legacy-readiness --snapshot`

When health checks fail:
- Raise alert with failing check names
- Do not silently continue in degraded canonical mode

---

## 4) OpenClaw integration mode

Use OpenClaw bridge plugin wiring for lifecycle hooks:
- `agent_end` -> canonical turn path
- compaction hooks -> canonical flush path

Operator command reference:
- `core-memory openclaw onboard`
- `core-memory openclaw onboard --replace-memory-core` (only when explicitly desired)

---

## 5) Legacy-path policy

- `trigger_orchestrator` is compatibility-only; audit usage.
- `write_triggers` is compatibility-only and must remain disabled unless explicitly allowed.
- `openclaw_integration` compatibility wrappers are transitional.

Environment guardrails:
- Strict shim block mode may be enabled via:
  - `CORE_MEMORY_BLOCK_LEGACY_TRIGGER_ORCHESTRATOR=1`
- Legacy write triggers remain blocked by default unless explicitly enabled.

---

## 6) Operator-facing docs (authoritative references)

- `docs/canonical_contract.md`
- `docs/integrations/openclaw/plugin-setup.md`

If this file conflicts with those docs, treat those docs as source of truth and update this file immediately.
