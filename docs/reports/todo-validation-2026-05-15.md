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

## Architectural guardrails per TODO item

These express what each implementation must and must not do to keep the final architecture clean. They apply regardless of which phase or slice does the work.

---

### TODO #2 — Goal lifecycle resolution

**Goals**
- A dedicated resolution pass runs after each turn's enrichment and asks: does this turn's outcome bead relate to any open `candidate` goal bead?
- Matching produces a typed association (`resolves` or `outcome_of`) routed through the same association delta path as all other associations — not a bespoke write.
- On a match, goal status transitions `candidate → resolved` via the existing promotion machinery (`promotion_contract.py`), not a new state machine.
- Resolution must be evidence-grounded: the association must carry the turn ID and visible window that caused it, so the decision is auditable and replayable.
- Heuristic matching (tag/semantic overlap) is sufficient for the first cut; LLM escalation is optional and additive, not required.

**Non-goals**
- Goal resolution must not own semantic policy. It consumes bead content and emits associations; it does not judge what a bead means.
- Must not mutate bead content — only associations and promotion state.
- Must not silently expand the visible window. Cross-session goal matching requires explicit cross-session scope to be opted into; the resolution pass must not paper over the `visible_bead_ids` constraint without the window merge prerequisite being in place first.
- Must not be a general outcome classifier. It answers one question: does this outcome close a goal? Nothing more.
- The detection mechanism cannot live in OpenClaw or any plugin bridge. It belongs in `core_memory/runtime/` or `core_memory/policy/`.

---

### TODO #3 — Association relationship types

**Goals**
- Richer types are assigned by `association/preview.py` (the upstream heuristic), not only validated at the delta envelope level. Enum enforcement at the delta is a correctness gate, not a substitute for upstream type assignment.
- The 4-type set expands to at minimum: `associated_with` (replaces `related` as the generic fallback), `caused_by` / `led_to` (cross-session causal signal), `precedes` (temporal direction opposite to `follows`).
- `reason_code` or `reason_text` on each preview entry carries the heuristic name that produced the type, keeping the relationship label itself canonical and policy-free.
- All emitted types must be within the 28-type schema already defined. No new types invented outside that enum.

**Non-goals**
- Must not require an LLM call for every association. Heuristic expansion comes first; LLM classification is an optional escalation path for ambiguous cases, not the default.
- Relationship type policy must not move into OpenClaw, plugin bridge code, or the delta contract itself. The delta validates; `association/preview.py` decides.
- Must not change association persistence or dedupe semantics. Only the `relationship` field value changes; the `(source, target, relationship)` dedupe key structure stays the same.
- Must not break the `excluded` set in `engine.py:372` silently — review it explicitly when `associated_with` replaces `related` and when `caused_by`/`led_to` are added.

---

### TODO #5 — Grounding hashes for claim idempotence

**Goals**
- Every judged edge or claim validation carries a `grounding_hash` computed from: sorted evidence bead IDs, judge model identifier, and prompt/rubric version. No volatile fields (timestamps, generated IDs) enter the hash.
- Before writing a new verdict, the enrichment path checks whether an update with the same `(target_claim_id, grounding_hash)` already exists. If it does, the write is skipped — idempotent re-validation.
- The hash is stored on claim update rows and on judged association edges, making the evidence provenance inspectable without re-running the judge.

**Non-goals**
- Grounding hashes are not a content-equality gate for beads — that is `dedupe_key`'s job. The two mechanisms solve different problems and must not be conflated.
- Must not prevent re-judging. The hash enables detection and deduplication of redundant judgments; it does not lock evidence.
- Must not hash bead content directly — only bead IDs. Content changes are a separate bead-mutation concern.
- This is not a replacement for monotonic sequencing (#6). Grounding hashes answer "was this exact evidence already judged?" Sequencing answers "which verdict wins when two arrive out of order?" Both are needed; neither substitutes for the other.
- Must not be added only at the delta layer. The hash must be stored in the canonical `store_claim_ops.py` write path so it is present regardless of whether the delta adapter is in use.

---

### TODO #6 — Monotonic sequencing for supersede chains

**Goals**
- A `chain_seq` integer counter is maintained per `(subject, slot)` pair. It is read-then-increment on each claim update write, under the existing store lock, so it reflects append order independent of async job completion time.
- `resolve_current_state` in `store_claim_ops.py` sorts updates by `chain_seq` (falling back to append order for legacy records) before applying supersede/retract/conflict decisions. The `active_claims[-1]` selection becomes `active_claims[-1]` after that sort — deterministic regardless of how jobs arrived.
- Legacy records without `chain_seq` degrade gracefully to current behavior rather than erroring.

**Non-goals**
- `chain_seq` is not a global sequence counter. It is scoped to `(subject, slot)`. A counter shared across all claims would create lock contention and a false ordering relationship between unrelated slots.
- Must not replace `dedupe_key`. Sequencing governs write order and resolution priority; identity deduplication is a separate concern.
- Must not require reading the full claim history on every write to assign the counter. A lightweight per-slot high-water-mark read is sufficient; a full scan is not acceptable on the hot path.
- Must not be confused with grounding hashes (#5). Sequencing is about which verdict was intended to be authoritative. Grounding is about whether a judgment was already made on this exact evidence. Both are needed independently.

---

### TODO #7 — Semantic indexing CLI ergonomics

**Goals**
- A top-level `core-memory semantic` subcommand group with four commands: `status`, `rebuild`, `tail`, `doctor`.
- `status` reads the manifest and queue files (`lifecycle.py`) and returns a single parseable JSON object — dirty state, last dirty reason, queue epoch, mode, and last checkpoint. No side effects.
- `rebuild` enqueues a delta or reconcile rebuild via `enqueue_semantic_rebuild()` and optionally drains the worker immediately. Accepts `--mode delta|reconcile`.
- `tail` reads the last N entries from the semantic event log or summarizes manifest + queue state if no log exists. No side effects.
- `doctor` surfaces the existing `semantic_doctor()` output from `semantic_index.py`. The new command delegates to the existing function; it does not duplicate its logic.
- All four commands output JSON so they are scriptable and testable without parsing human-readable text.

**Non-goals**
- The CLI commands must not own semantic policy. They surface existing lifecycle functions; they do not make indexing decisions.
- Must not replace `graph semantic-doctor`. The new `semantic doctor` command wraps the same function; the `graph` alias can remain for backward compatibility.
- `semantic status` must produce no writes or side effects. It is a pure read.
- Must not introduce a new semantic backend or change the embedding provider contract. This is purely a CLI surface over existing infrastructure.
- Must not block on indexing work inline unless `--wait` is explicitly passed to `rebuild`.

---

### Cross-cutting guardrails (all TODOs)

- **Semantic policy stays in Core Memory.** No relationship type, claim decision, goal resolution, or sequencing rule may live in OpenClaw, plugin bridge code, or the delta contract itself. The delta contract validates shape; Core Memory decides meaning.
- **The `window_bead_ids` merge is a prerequisite for #2 and #6, not part of either.** Cross-session claim comparison and goal resolution both depend on recalled context being visible to the decision pass. This union (`visible_bead_ids ∪ window_bead_ids` before `emit_claim_updates`) must land as its own atomic change before either TODO's implementation begins.
- **Direct write helpers remain available.** `write_claims_to_bead`, `write_claim_updates_to_bead`, `write_memory_outcome_to_bead` stay as low-level storage primitives for tests and legacy callers. Higher-level paths routing through the delta adapter must not remove or deprecate them.
- **Cross-session scope is always explicit and opt-in.** No implementation may silently expand the visible window beyond the current session. Any cross-session scope must be expressed through `window_bead_ids` (caller-supplied recalled context) or a future explicit historical scope flag — never as a default.
- **Each TODO lands as an independent, testable slice.** No TODO implementation should be bundled with another in a single commit sequence. The delta adapter enforces this: reserved row types stay reserved until their owning slice is proven stable in isolation.

---

## Immediate gaps before next phase work

Three issues in the Slice A cleanup commit (`c171612`) need to close before Phase 5 begins:

1. **Dead normalization functions** — `_normalize_delta_claim_row`, `_normalize_delta_claim_update_row`, `_normalize_entity_upsert_row`, `_normalize_goal_lifecycle_row`, `_normalize_memory_outcome_row` remain in `session_enrichment_delta.py` with zero callers. `_normalize_entity_upsert_row` has a partial edit (`source_bead_id` removed) that diverges from its intended schema.
2. **`DELTA_ROW_LIMITS` misleading entries** — reserved row types still carry live limits (`claims: 128`, etc.) despite `_bounded()` never being called for them. Should be `0`.
3. **`source_outside_visible_window` quarantine untested** — the new source-side visibility check has no test; only the target-side case had prior coverage.
