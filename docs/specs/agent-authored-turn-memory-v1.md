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

## Turn-time agent invocation (slice 3)

When `metadata.crawler_updates` is absent, runtime can invoke a crawler agent callable before association apply.

Environment controls:
- `CORE_MEMORY_AGENT_CRAWLER_INVOKE`
- `CORE_MEMORY_AGENT_CRAWLER_CALLABLE` (`module:function`)
- `CORE_MEMORY_AGENT_CRAWLER_MAX_ATTEMPTS` (bounded retries)

Deterministic invocation errors:
- `agent_callable_missing`
- `agent_invocation_exhausted`

## Association quality policy (slice 4)

- Deterministic preview association promotion is disabled by default:
  - `CORE_MEMORY_PREVIEW_ASSOC_PROMOTION=0` (default)
- `shared_tag` preview relation is blocked by default even when preview promotion is enabled:
  - `CORE_MEMORY_PREVIEW_ASSOC_ALLOW_SHARED_TAG=0` (default)
- Canonical temporal direction for new inferred edges is `follows`; non-canonical temporal forms are quarantined under strict policy.
- In strict agent-authored mode, non-initial turns require minimum non-temporal semantic association coverage:
  - `CORE_MEMORY_AGENT_MIN_SEMANTIC_ASSOC_AFTER_FIRST` (default `1`)
  - violation code: `agent_semantic_coverage_missing`

## Association lifecycle overlay + recovery (slice 5)

Append-only history is preserved while allowing current-truth edge state to evolve.

Lifecycle actions (queued via crawler updates):
- `retract`
- `supersede` (with `replacement_association_id`)
- `reaffirm`

Association status fields:
- `status` (`active|retracted|superseded`)
- `superseded_by_association_id`
- `supersedes_association_id`

Default projection behavior uses active associations only.

Operational visibility:
- `core-memory graph association-health [--session-id ...]`

## Telemetry + SLO gates (slice 6)

Per-turn quality telemetry is emitted as metrics rows with:
- `task_id=agent_turn_quality`
- source/fallback/block diagnostics
- association mix counts (`shared_tag`, temporal, non-temporal semantic)

SLO report/check surfaces:
- `core-memory graph association-slo-check`

Primary gate dimensions:
- agent-authored source rate
- fallback rate
- fail-closed rate
- average non-temporal semantic associations per successful turn
- active shared_tag ratio in current graph view

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
