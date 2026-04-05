# Agent-Authored Turn Memory Contract v1 (Slice 0 Lock)

## Intent

Per-turn memory semantics must be authored by the agent, not silently fabricated by deterministic fallback logic.

This contract separates:

- **Semantic authorship** (agent responsibility)
- **Clerical persistence** (runtime/store responsibility)

---

## Agent-authored required fields (per turn)

For the current-turn bead row:

- `type`
- `title`
- `summary`

Shape requirement:
- `beads_create` must contain exactly **one** current-turn bead row.

For inferred semantic associations:

- `source_bead_id`
- `target_bead_id`
- `relationship`
- `reason_text`
- `confidence`

Shape requirement:
- `associations` must be present and non-empty in strict mode.

---

## Clerical/runtime-assigned only

The runtime/store may assign:

- `id`
- timestamps (`created_at`, `updated_at`)
- session linkage (`session_id`, `source_turn_ids`)
- index/merge bookkeeping
- projection ownership metadata (`cm_owner`, `cm_dataset`)

These do not replace semantic authorship.

---

## Strictness flags

- `CORE_MEMORY_AGENT_AUTHORED_REQUIRED`
  - When enabled, runtime should require agent-authored payloads for semantic turn memory.
- `CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN`
  - When enabled, runtime may fallback when payload is missing/invalid.
  - Intended default for strict production posture is disabled (`0`).

Slice 1 introduces runtime gating and strict/fail-open behavior.
Slice 2 hardens strict payload shape validation.

---

## Error code contract (scaffolded)

- `agent_updates_missing`
- `agent_updates_invalid`
- `agent_associations_missing`
- `agent_bead_fields_missing`

These codes are reserved for strict-mode enforcement and diagnostics.

---

## Out-of-scope for slice 0

- Runtime behavior change
- Quarantine/fail-open execution branching
- Mandatory invocation of turn-time crawler model pass
- Distribution quality gates

Those are implemented in subsequent slices.
