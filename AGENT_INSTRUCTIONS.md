# AGENT_INSTRUCTIONS.md (Canonical Automation Instructions)

Status: **Canonical**
Date: 2026-03-24

Purpose: Single root-level automation contract for agent/heartbeat/session-end flows.

---

## 0) Core rule
Always follow canonical runtime ownership:
- Turn path authority: `memory_engine.process_turn_finalized`
- Flush path authority: `memory_engine.process_flush`

Never route primary automation through deprecated shim paths.

---

## 1) Unified bead-writing contract

A bead is the canonical record for a turn.
It is not primarily a conversation summary.

Every turn must produce exactly one bead so the session remains temporally connected.

Some beads are thin:
- only minimum required fields are populated
- they preserve chronology, replay, and temporal traversal
- they are usually not retrieval-eligible

Some beads are rich:
- they include structured retrieval fields
- they carry durable semantic value
- they may be retrieval-eligible

The distinction is not bead type.
It is field completeness and retrieval eligibility.

Summary is optional.
Do not add vague prose just to satisfy a field.
Prefer structured retrieval fields over summary text.

---

## 2) On every turn-finalized / `agent_end`

Required actions:
1. Emit finalized turn into canonical ingestion.
2. Ensure one current-turn bead is written.
3. Ensure temporal grounding is present for the bead.
4. Decide retrieval eligibility from payload quality.
5. Run promotion-state decision pass across visible session beads.

Required invariants:
- Idempotent by `(session_id, turn_id)`.
- Exactly one bead exists per `(session_id, turn_id)`.
- Promotion monotonicity: once `promoted`, never demote/unpromote.
- No compaction on turn path.
- Thin beads are valid and must not be forced into retrieval.

Temporal minimum surface on initial write:
- `session_id`
- `source_turn_ids`
- `turn_index` when available
- `prev_bead_id` when available

---

## 3) Association contract

At initial write, only temporal association is required:
- session membership
- turn order
- predecessor linkage when available

Do not force broad causal/semantic associations on initial write.
Non-temporal associations may be appended later via stronger evidence from session analysis.

Never invent weak associations to make the graph look complete.

---

## 4) Retrieval eligibility contract

Default retrieval should prioritize beads where:
- `retrieval_eligible = true`

Thin beads still matter for:
- timeline
- replay
- audit
- temporal traversal
- deep recall expansion

`retrieval_eligible = true` requires structured payload quality:
- non-generic title
- `retrieval_title`
- at least one `retrieval_fact`
- at least one quality signal (`because`, `supporting_facts`, `state_change`, `evidence_refs`, `supersedes`, `superseded_by`)

If quality is insufficient, normalize to:
- `retrieval_eligible = false`

---

## 5) Admissibility / richness rules

Every turn still writes one bead.
Bead richness is classified as:
- `LOW` -> thin bead, usually `retrieval_eligible=false`
- `NORMAL` -> enriched bead, may be `retrieval_eligible=true`

If content is pure runtime/meta chatter, write the thinnest possible bead and keep it non-eligible for retrieval.

A bead should answer at least one of:
- what changed?
- what was decided?
- what caused this?
- what evidence supports this?
- what is currently valid?
- what superseded what?

If none apply, write only the minimum temporal bead.

---

## 6) On memory flush cycle only

Required sequence:
1. Archive compaction (session scope, full pre-compaction snapshot authority)
2. Rolling-window maintenance write
3. Archive compaction (historical scope)
4. Commit flush checkpoint and report artifact

Required invariants:
- Flush runs once per latest processed turn/cycle (duplicate flush must skip).
- Flush report stages must include committed/skipped/failed as applicable.
- Archive is full-context retrieval authority.
- Rolling window is derived injection surface and token budget manager.

---

## 7) Health checks during automation windows

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

## 8) OpenClaw integration mode

Use OpenClaw bridge plugin wiring for lifecycle hooks:
- `agent_end` -> canonical turn path
- compaction hooks -> canonical flush path

Operator command reference:
- `core-memory openclaw onboard`
- `core-memory openclaw onboard --replace-memory-core` (only when explicitly desired)

---

## 9) Legacy-path policy

- `trigger_orchestrator` is compatibility-only; audit usage.
- `write_triggers` is compatibility-only and must remain disabled unless explicitly allowed.
- `openclaw_integration` compatibility wrappers are transitional.

Environment guardrails:
- Strict shim block mode may be enabled via:
  - `CORE_MEMORY_BLOCK_LEGACY_TRIGGER_ORCHESTRATOR=1`
- Legacy write triggers remain blocked by default unless explicitly enabled.

---

## 10) Operator-facing docs (authoritative references)

- `docs/canonical_contract.md`
- `docs/integrations/openclaw/plugin-setup.md`

If this file conflicts with those docs, treat those docs as source of truth and update this file immediately.
