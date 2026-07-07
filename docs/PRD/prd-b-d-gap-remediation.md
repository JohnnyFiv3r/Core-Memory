# PRD: Memory-Quality Meters + Self-Model Authoring — Gap Remediation (engine slice)

**Status:** Complete — implemented by Core Memory #352/#353. The consuming surface
companions shipped in Satorid #253/#254; calibration relocation remains optional cleanup.
**Scope:** the Core Memory engine changes that close the contract gaps in two
"substantially complete" capability PRDs — **PRD-B** (three memory-quality meters) and
**PRD-D** (agent self-model authoring). The surface/agent companions (scheduling the meter
probe, the chat-invoked edit tool) live in the consuming surface's remediation plan and are
out of scope here.

---

## Problem

A verification pass against the merged code found both capabilities substantially built but
with specific gaps against their own contracts:

- **PRD-B — meters.** Meter 1 (calibration curve) and Meter 3 (self-model drift) are faithful.
  **Meter 2 (tension-resolution) is not:** it reads only `build_soul_summary().persistent_tensions`
  and never consults the dreamer candidates queue that its contract specifies; it emits a status
  enum the schema does not define; and it drops a history guard.
- **PRD-D — authoring.** Signal enrichment, confidence-gated authority, the goal-lifecycle tie,
  and the read-side (`GET /v1/soul/files*`) are done. **Two engine gaps remain:** the pruning-flag
  mechanism is only half-wired, and authoring decisions never feed the myelination reward loop.

---

## Original gap state (before remediation)

| Location | State |
|---|---|
| `runtime/observability/tension_meter.py` | Meter 2 — soul-summary-only; status `healthy \| accumulating \| stalled`; `zero_resolution` = `new_rate>0 and resolution_rate<=0` (no history guard) |
| `soul/dreamer_bridge.py:184-197` | `contradiction_present` → `candidate_only` routing present; no `pruning_flag`/`needs_pruning`/`skipped_stale_divergence` |
| `soul/store.py` (`approve_soul_update`/`reject_soul_update`) | No reward-event emission on authoring decisions |
| `persistence/myelination_rewards.py` | `reward_dreamer_candidate_decision` helper **exists** (edge-keyed, idempotent) — not yet called from authoring |
| `persistence/calibration.py:182` | Meter 1 `compute_calibration_curve` — correct, but located in `persistence/`; `runtime/observability/calibration.py` is a 7-line compat shim |

---

## Design / changes

### C1 — Meter 2 to contract  *(P0)*
`runtime/observability/tension_meter.py`:
1. **Read the dreamer candidates queue** (`.beads/events/dreamer-candidates.json`) for
   `pending_count` + terminal `accepted/deferred/rejected` counts, per the meter contract's
   §5.2 steps 2–3. Stop deriving those from `persistent_tensions` statuses (which conflates
   `active` tensions with accepted candidates). `new_tension_rate`/`resolution_rate` continue
   to come from `build_soul_summary()`. *(Meter 3, `self_model_drift.py`, already reads this
   queue via `_accepted_divergence_for_key` — reuse the same loader.)*
2. **Status enum** → the contract's `healthy | stale_accumulation | high_accumulation |
   zero_resolution` (do not collapse to `accumulating`/`stalled`). The `flags[]` array keeps
   the same three flag names.
3. **`zero_resolution` guard:** only flag when `new_tension_rate > 0` AND `resolution_rate = 0`
   AND the lookback contains **≥ 7 days of history** (new gate, configurable).

### C2 — Self-model pruning path + authoring→reward loop  *(P1)*
`soul/dreamer_bridge.py` + `soul/store.py`:
1. **Pruning-flag mechanism:** the authoring prompt sets `needs_pruning`/`pruning_reason` on
   contradiction or supersession; normalize to `metadata.pruning_flag`; **any `pruning_flag`
   forces `candidate_only`** (never auto-write). Add `skipped_stale_divergence` handling for
   `identity_divergence_candidate`s whose key no longer has a supporting entry.
2. **Reward loop:** wire the existing `reward_dreamer_candidate_decision`
   (`persistence/myelination_rewards.py`) into `approve_soul_update` / `reject_soul_update` so
   an authoring decision emits an edge-keyed `dreamer_candidate_decision` reward event —
   **positive on approve, negative on reject** — respecting the edge-only invariant and the
   SHA-256 idempotency fingerprint. This reinforces the manifest that the authoring enrichment
   and the calibration meter both consume, closing the self-model reward loop.

### C3 — Relocate calibration  *(P2, optional)*
Move `compute_calibration_curve` from `persistence/calibration.py` to
`runtime/observability/calibration.py` (its meter-contract home, beside the other two meters),
or keep the compat shim with an explicit note. Persistence computing over retrieval-feedback +
soul is a layering inversion; non-blocking.

---

## Non-goals (tracked in the surface plan)
- **Scheduling the meter probe** so the primary success metric (per-workspace calibration
  Spearman ρ) is logged per nightly run — surface/cron concern.
- **The chat-invoked governed edit tool** for SOUL files (dry-run → confirm → apply) — surface
  agent-service concern. The engine already exposes the endpoints it needs.

---

## Acceptance criteria
- **Meter 2:** `pending_count` and terminal accepted/deferred/rejected are **queue-derived**;
  `status` is one of the four contract values; `zero_resolution` never fires with < 7 days of
  history.
- **Pruning:** a finding with `pruning_flag` set is never auto-written (forced `candidate_only`);
  a stale `identity_divergence_candidate` is `skipped_stale_divergence`.
- **Reward loop:** `approve_soul_update` emits a positive `dreamer_candidate_decision` reward
  event with concrete `edge_keys`; `reject_soul_update` emits a negative one; re-running is
  idempotent.
- Full suite green, incl. `tests/test_public_generic_naming`.

## Tests
- Meter 2: queue-derived terminal counts; status-enum conformance; sub-7-day → no `zero_resolution`.
- Pruning: `pruning_flag` forces `candidate_only`; stale divergence skipped.
- Reward: approve/reject → reward event with `edge_keys`, correct polarity, idempotent fingerprint.

## Rollout
Independent changes; C1 and C2 can land in either order. C2's reward emission feeds the
manifest, so it complements (does not depend on) the meter work. C3 is opt-in cleanup.

## Completion notes

- C1 shipped in Core Memory #352: Meter 2 counts are candidate-queue-derived, the status enum
  matches the contract, and `zero_resolution` is guarded by seven days of history.
- C2 shipped in Core Memory #353: pruning flags force `candidate_only`, stale divergence is
  skipped, and SOUL approve/reject emits idempotent authoring reward receipts when edge evidence
  exists.
- The Satorid companions shipped in #253/#254: quality-meter probing is scheduled and logged,
  and the governed chat-invoked SOUL edit tool is intent-gated, dry-run-first, and
  confirmation/idempotency-gated for apply.
