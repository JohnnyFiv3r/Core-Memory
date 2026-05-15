# Core-Memory TODO Validation Report

**Date:** 2026-05-15
**Branch assessed:** `fix/causal-support-instructions`
**Source of truth:** live code only — no reliance on git history or commit messages

---

## Status by item

| # | Item | Code status | Notes |
|---|------|-------------|-------|
| 1 | LLM-extracted `because` reasoning | **Closed** | Confirmed in `bead_judge.py`, `rationale.py` |
| 2 | Goal lifecycle resolution | **Not implemented** | No detection pass, no outcome→goal association creation, no status transition anywhere in the codebase |
| 3 | Association relationship types | **Partial** | 28 types defined in schema; `association/preview.py:68–74` emits only 4 (`related`, `supports`, `shared_tag`, `follows`). Delta enum enforcement (`1e2a39b`) validates inputs but does not expand what upstream code produces |
| 4 | Question classification guardrail | **Closed** | Confirmed in `bead_typing.py:95,189` |
| 5 | Grounding hashes for claim idempotence | **Not implemented** | No `grounding_hash` field anywhere in `enrichment.py`, `turn_flow.py`, or `store_claim_ops.py`. Explicitly excluded from the #9 plan scope |
| 6 | Monotonic sequencing for supersede chains | **Partial** | Supersede logic exists; `store_claim_ops.py:329` picks `active_claims[-1]` by list order. No `chain_seq` counter. Async out-of-order completion is unguarded |
| 7 | Semantic indexing CLI ergonomics | **Partial** | Delta queue, dirty marking, manifest, vector backend adapters, `semantic-doctor`, and worker drain path exist. `semantic-reconcile` job kind added (`d9212ef`). Missing: top-level `core-memory semantic status`, `semantic rebuild`, `semantic tail` — only `graph semantic-doctor` and `ops jobs run semantic-rebuild` exist |

---

## #9 plan phase coverage

The `fix/causal-support-instructions` branch implements Phases 0–4 of the nine-phase `session_enrichment_delta.v1` plan. Phases 5–9 map to the open TODOs as follows:

| Phase | Goal | Covers TODO | Gap |
|-------|------|-------------|-----|
| 5 | Fold association types | **#3** — direct | None; plan matches TODO |
| 6 | Fold entity registry | *(no open TODO)* | Infrastructure only |
| 7 | Fold claims + sequencing | **#6** — direct | Monotonic sequencing is specified; grounding hashes (#5) are not |
| 8 | Fold goal lifecycle | **#2** — partial | Delta schema and lifecycle item are specified; the detection mechanism (how an outcome bead is matched to an open goal bead) is unspecified in the plan |
| 9 | Semantic indexing | **#7** — direct | None; plan names `doctor/status commands` explicitly |

**TODO #5 (grounding hashes) has no corresponding phase.** The Phase 1 analysis doc explicitly marks it out of scope: *"No full #5 grounding-hash validation or #6 benchmark/eval layer."* It will remain open after all nine phases land.

---

## Key architectural finding

The "building over time" concern — claims and associations not accumulating across sessions — has a specific mechanical cause not addressed by any current or planned commit:

`emit_claim_updates()` compares new claims against `visible_bead_ids`, which is populated from `_session_visible_bead_ids()` (current session only). `window_bead_ids` (recalled cross-session context) is already threaded through the full request shape — `ingress.py`, `turn_flow.py`, `enrichment.py` — but is not merged into the visible window before the claim decision pass runs.

Until that union is made, a supersede or reaffirm in session N cannot find and act on a claim from session 1, even when that session-1 bead was recalled into context. This is not covered by Phase 7 as written and should be a prerequisite for both Phase 7 (claim sequencing) and Phase 8 (goal lifecycle).

---

## Immediate gaps before next phase work

Three issues in the Slice A cleanup commit (`c171612`) need to close before Phase 5 begins:

1. **Dead normalization functions** — `_normalize_delta_claim_row`, `_normalize_delta_claim_update_row`, `_normalize_entity_upsert_row`, `_normalize_goal_lifecycle_row`, `_normalize_memory_outcome_row` remain in `session_enrichment_delta.py` with zero callers. `_normalize_entity_upsert_row` has a partial edit (`source_bead_id` removed) that diverges from its intended schema.
2. **`DELTA_ROW_LIMITS` misleading entries** — reserved row types still carry live limits (`claims: 128`, etc.) despite `_bounded()` never being called for them. Should be `0`.
3. **`source_outside_visible_window` quarantine untested** — the new source-side visibility check has no test; only the target-side case had prior coverage.
