# Core Memory — Canonical TODO

**Last updated:** 2026-05-30

Single source of truth for open capability work. Engine-correctness items #1–#9 are
closed — see `docs/status.md` for the record. Capability items #10–#14, #16–#17 are
closed. Open: #15 (multi-store fan-out).

Detailed PRDs for #10–#14: `docs/reports/capability-roadmap-prds.md`
Validation snapshot (2026-05-15): `docs/reports/todo-validation-2026-05-15.md`

---

## Previously-open prerequisite items — now closed

### #0 — `visible_bead_ids ∪ window_bead_ids` merge

**Status: Closed — already implemented**

The merge is in place at `turn_flow.py:423-431` and `enrichment.py:203-205`:

```python
claim_visible_ids = sorted(
    set(visible_ids + [str(x) for x in (req.get("window_bead_ids") or []) if str(x).strip()])
)
```

This was listed as missing in the May 15 validation report but landed before the
May 28 status update. No action required.

### #2 — Goal lifecycle resolution

**Status: Closed — already implemented**

`core_memory/runtime/session/goal_lifecycle.py` exists with outcome→goal
detection:
- `_match_goal()` matches on shared tags or ≥2 shared tokens
- Bead type `"outcome"` triggers the resolution pass; `"goal"` beads are candidates
- Matched pairs produce a `resolves` association through the standard delta path
- Status transitions via `promotion_contract.py`

Confirmed closed in `docs/status.md` (2026-05-28).

---

## Open capability items (recommended build order)

### #9 Slice B — Session enrichment delta envelope

**Status:** Complete  
**Blocks:** nothing  
**Effort:** ~3 days  

**Shipped:**
- `enrichment_run_id: str | None` param on `run_turn_enrichment()`; auto-generates
  UUID when caller omits it (`runtime/passes/enrichment.py`)
- Idempotency gate at entry: checks `.beads/events/enrichment-{bead_id}-{run_id[:8]}.jsonl`;
  if found reads last line and returns `{idempotent: True, stage_results: ...}` without
  re-running any stage
- `stage_results` dict (9 canonical keys) threaded through all stages; each stage
  populates its slot from the function's return value
- Delta envelope (`session_enrichment_delta.v1`) persisted after Stage 9 with `bead_id`,
  `session_id`, `enrichment_run_id`, `triggered_at`, `completed_at`,
  `idempotency_token` (SHA-256 of bead+run), and full `stage_results`
- Stage 4 (crawler merge) atomicity confirmed: `merge_crawler_updates` already holds
  `store_lock` across both the index write and log clear; documented in comment
- `enrichment_run_id` threaded through the queued job path in `side_effect_queue.py`
  so job replays are also idempotent
- `_enrichment_envelope_path()` and `_run_idempotency_token()` helpers exported
- 15 tests in `tests/test_enrichment_slice_b.py`, all passing

---

### #13 — Temporal recall API (`as_of`)

**Status:** Complete  
**Blocks:** nothing; blocked by nothing  
**Effort:** ~2 days  
**Spec:** `docs/reports/capability-roadmap-prds.md` § #13

**Shipped:**
- `recall()` accepts `as_of: str | None`; validates via `normalize_as_of()` and raises
  `ValueError` with an ISO 8601 message on bad input (`retrieval/agent.py`)
- `as_of` threaded through `request_overrides` into `memory_execute` and the canonical
  pipeline (`retrieval/pipeline/canonical.py`), which passes it to `resolve_all_current_state()`
- Post-filter: `_filter_evidence_by_as_of()` drops `EvidenceItem`s whose
  `metadata["created_at"]` is after `as_of`; items with no `created_at` pass through
- k×1.5 inflation: when `as_of` is set, the k sent to `memory_execute` is inflated by
  1.5× (rounded, capped at 50) to compensate for post-filter shrinkage
- `result.as_of` (dataclass field) and `result.metadata["as_of"]` both set on the result
- CLI `core-memory recall --as-of TIMESTAMP` (`cli/__init__.py`, handler passes through)
- `POST /api/recall` body field `as_of` accepted and applied (`demo/app.py`)
- 15 tests in `tests/test_recall_as_of.py` covering filter logic, validation, result
  field population, k inflation per effort level, cap, and no-as_of baseline

---

### #11 — Myelination wiring

**Status:** Complete  
**Blocks:** #12 (myelination signal now available)  
**Effort:** ~2 days  
**Spec:** `docs/reports/capability-roadmap-prds.md` § #11

**Shipped:**
- `record_retrieval_feedback()` called fire-and-forget after every `recall()` in
  `retrieval/agent.py`; records edges, outcome, and intent from the raw response
- `_read_myelination_manifest()` + `_apply_myelination_bonuses()` in `agent.py` read
  the pre-computed manifest and adjust evidence scores before return
  (`new_score = min(1.0, max(0.0, base_score + bonus))`); sorted by adjusted score
- `apply_contradiction_decay(root, bonus_by_bead_id)` in
  `runtime/observability/myelination.py` — scans `resolve_all_current_state()` for
  `status: conflict` slots; reduces source-bead bonus by `neg_cap` (clamped to
  `[-neg_cap, pos_cap]`); exception-safe (returns map unchanged on resolver failure)
- `"myelination-update"` job in `runtime/queue/side_effect_queue.py` — calls
  `compute_myelination_bonus_map()` then `apply_contradiction_decay()`, writes manifest
  to `.beads/events/myelination-manifest.json`; enqueue-able via `enqueue_async_job()`
- CLI `core-memory myelination report` and `core-memory myelination status`
  (`cli/__init__.py`)
- 21 tests in `tests/test_myelination_wiring.py` covering bonus application, manifest
  read/write, job enqueue, decay logic (positive/zero/clamped/non-conflict/no-bead-id),
  and resolver exception handling

---

### #10 — Multi-speaker attribution and identity persistence

**Status:** Complete  
**Blocks:** nothing; blocked by nothing  
**Effort:** ~1 day research + ~3 days implementation  
**Spec:** `docs/reports/capability-roadmap-prds.md` § #10  
**Research artifact:** `docs/plans/speaker-schema-research.md`

`transcript_ingest.py` previously recorded `user_speaker`/`assistant_speaker` as opaque
strings with no identity resolution. Causal chains broke at participant boundaries — "we
decided to drop Kubernetes" was unattributable when there was no mechanism to link
`johnnyfiv3r` (Discord), `johnny` (Slack), and `jf@company.com` (email) to a single
entity across sessions.

**Shipped:**
- `entity/speaker_resolver.py` — `resolve_speaker(index, observed_label, source_system)`
  returning `SpeakerResolution`; confidence model (1.0 exact alias match, 0.9 new entity,
  0.0 invalid label)
- `transcript_ingest._resolve_envelope_speakers()` wires the resolver before
  `ingest_turn_envelopes()`; attaches `speaker_attribution` to envelope metadata and
  persists newly created entities via `save_entity_registry()`
- `SpeakerAttribution` dataclass in `schema/models.py`
- `store_add_bead_ops.py` promotes `attributed_entity_id` + `resolution_confidence` to
  the bead top level when a resolved-speaker turn is written
- `register_speaker_alias()` on `entity/registry.py`
- `SPEAKER_RESOLUTION_CONFIDENCE_THRESHOLD` env var, default 0.75

**Design note (role vs. identity):** role (`user`/`assistant`) is a *declared* structural
channel — `_normalize_role()` is a pure dict lookup, never agent-judged — while speaker
identity is the orthogonal axis #10 resolves. From zero context the system never infers
who "the user" is; the adapter declares the role, and in a 1:1 agent chat `user` is the
non-agent party by construction. When a turn carries no speaker label, no
`attributed_entity_id` is written (attribution is opt-in on the presence of a real label).

---

### #10A — Multi-party transcript ingest (N-speaker gateway)

**Status:** CLOSED

**Shipped:**
- `normalize_transcript_payload()` `mode="group"` path via `_group_envelopes()` — accepts
  N-speaker turns with per-row `speaker` label; unknown roles map to `"other"` via
  `_normalize_role_group()`; utterances windowed into envelopes without forcing
  user/assistant pair collapse
- Per-row `speaker` carried into canonical envelope `turns[]` for independent resolution
  by `_resolve_envelope_speakers()`
- Dyadic path (`mode="dyadic"`) unchanged — no regression for 1:1 agent chats
- `_ALLOWED_MODES = {"dyadic", "group"}` enforced at entry

---

### #10B — Per-adapter `source_system` convention (MCP ingest adapters)

**Status:** CLOSED

**Shipped:**
- `integrations/mcp/tools/ingest_slack.py` — Slack workspace export and API
  messages-list; `source_system="slack"`; stable user ID as speaker label; Unix epoch
  timestamps converted to ISO 8601 via `_slack_ts_to_iso()`
- `integrations/mcp/tools/ingest_discord.py` — DiscordChatExporter JSON; snowflake
  IDs as speaker labels; `source_system="discord"`
- `integrations/mcp/tools/ingest_zoom.py` — Zoom VTT + Otter.ai JSON; diarization
  labels scoped to recording (`source_system="zoom:{recording_id}"`); VTT relative
  timecodes are recording-relative offsets and not passed as absolute timestamps
- Integration tests in `tests/test_ingest_adapters.py` cover full ingest path without
  mocks: Slack Unix epoch timestamps, Zoom VTT relative timecodes, Discord ISO timestamps

---

### #14 — Contradiction pressure and epistemic uncertainty

**Status:** Complete  
**Blocks:** #12 (dreamer candidate type — now unblocked)  
**Effort:** ~3 days  
**Spec:** `docs/reports/capability-roadmap-prds.md` § #14

**Shipped:**
- `claim/epistemic.py` — `compute_epistemic_conflict_score(claim_a, claim_b, chain_seq_gap, time_delta_days) → float [0.0, 1.0]`
  and `conflict_score_for_pair()` convenience wrapper
- `ConflictItem` dataclass in `retrieval/contracts.py` (`subject`, `slot`,
  `claim_a_id`, `claim_b_id`, `epistemic_conflict_score`, `conflict_since`, `chain_seq_gap`)
- `RecallResult.conflicts: list[ConflictItem]` — additive; conflicted claims remain
  fully queryable; `conflicts` field is supplemental information
- `retrieval/agent._conflicts_for_result()` — scans evidence bead (subject, slot) pairs,
  calls `resolve_current_state()`, computes score, populates `result.conflicts`
- `runtime/dreamer/candidates.enqueue_contradiction_pressure_candidates()` — emits
  `contradiction_pressure_candidate` rows (score > threshold, strictly greater-than);
  threshold from `CORE_MEMORY_CONFLICT_REVIEW_THRESHOLD` env var (default 0.7)
- Fire-and-forget emission in `recall()` after `_enrich_recall_state`
- 28 tests in `tests/test_epistemic_conflict.py`

**Review UX (in-band conflict resolution):**
- `claim/conflict_review.py` — `build_conflict_review()` produces a *render-agnostic*
  prompt (both contested values, dates, a natural-language question, and resolution
  choices `prefer_a | prefer_b | retract_both | defer`). No button/card UI required —
  the agent speaks it, reads the user's free-text reply, and maps it to one choice.
- `resolution_to_claim_updates()` — maps a choice to canonical claim-update rows
  (`prefer_*` → supersede the loser; `retract_both` → retract both).
- `store_claim_ops.resolve_current_state()` — a supersede/retract issued *after* a
  conflict marker now clears it (so a resolution actually closes the conflict);
  simultaneous markers stay live.
- `decide_dreamer_candidate(resolution=...)` — `contradiction_pressure_candidate`
  apply branch writes the resolution through `emit_claim_updates` (audit turn via
  `process_turn_finalized`); `defer` records "not now" and writes nothing.
- `retrieval.agent._attach_conflict_reviews()` — links `candidate_id` + `review_prompt`
  onto above-threshold conflicts; suppresses prompts the user already deferred.
- `ConflictItem.candidate_id` + `ConflictItem.review_prompt` on the recall contract.
- `apply_reviewed_proposal` MCP surface gains `resolution`; agent guide documents the
  surface-and-resolve flow (`core-memory-agent-guide.md`).
- 22 tests in `tests/test_conflict_review.py`

---

### #14A — `both_valid` resolution + `context_scope` claim discriminator

**Status:** Complete  
**Blocks:** nothing  
**Effort:** ~2 days  

**Shipped:**
- `RESOLUTION_BOTH_VALID = "both_valid"` added to `RESOLUTION_CHOICES` in
  `claim/conflict_review.py`; exported in `__all__`
- `build_conflict_review()` — fifth resolution choice with scope-label effect text;
  `agent_instructions` updated with two-message loop guidance and `context_a`/`context_b`
  call signature
- `conflict_score_for_pair()` in `claim/epistemic.py` — returns `0.0` when
  `context_scope` values differ (cross-scope pairs are not conflicts)
- `resolve_all_current_state()` in `claim/resolver.py` — groups claims by
  `(subject, slot, context_scope or "")`: global claims (`scope=None/""`) key as
  `"subject:slot"` (backward-compat); scoped claims key as `"subject:slot::scope"`.
  Updates still looked up by base key so supersede rows reach both buckets.
- `Claim` dataclass in `schema/models.py` — `context_scope: str | None = None` field
  added; preserved through `from_dict`/`to_dict` serialization
- `decide_dreamer_candidate()` in `runtime/dreamer/candidates.py` — `scope_a`/`scope_b`
  params added; `both_valid` branch in `contradiction_pressure_candidate` section:
  - Returns `needs_clarification` (no write, candidate stays `unreviewed`) if either
    scope label is empty
  - On accept+apply with both scopes: writes fork-event bead via `process_turn_finalized`,
    writes two new context-scoped claims to fork bead via `write_claims_to_bead`,
    emits two supersede update rows via `emit_claim_updates`, returns
    `application_mode="context_scope_fork"` with `scope_a`, `scope_b`, `fork_bead_id`
- `apply_reviewed_proposal()` in `integrations/mcp/typed_write.py` — `context_a`,
  `context_b` params added; MCP schema updated with `context_a`/`context_b` properties
  and `"both_valid"` added to `resolution` enum
- Agent guide (`integrations/mcp/core-memory-agent-guide.md`) — `both_valid`
  two-message loop documented with complement-default (`context_b=""`) guidance
- 28 tests in `tests/test_both_valid_resolution.py`, all passing; pre-existing
  `test_conflict_review.py::test_has_four_resolution_choices` renamed to
  `test_has_five_resolution_choices`

Extends the conflict review UX from #14 with a fifth resolution choice: `both_valid`.
The existing four choices (`prefer_a`, `prefer_b`, `retract_both`, `defer`) assume the
conflict must eventually settle on one canonical value. `both_valid` handles the orthogonal
case where two claims are *both* true but in different contexts — e.g. prod on AWS, staging
on SQLite — and should coexist rather than compete.

#### Design decisions

- **`context_scope` defaults to `None` at write time.** Claims are born "global." Context
  is never inferred at ingestion; it is assigned retroactively, only by a `both_valid`
  resolution. Zero cost on the write path; existing claims and the extractor are untouched.
- **Scope vocabulary is bounded by actual resolutions.** No scope registry, no value
  normalization at claim-write time. Scope emerges from contradiction, which is precisely
  when it is needed.
- **`prefer_a`/`prefer_b` cover the synonym/canonicalization case.** "These are the same
  thing — pick the canonical spelling" is `prefer_a` with an informative reason string. No
  separate `canonicalize` resolution needed.
- **"Both are just true, leave it" is a no-op, not a defer.** If the user asserts both
  claims stand but refuses to name a scope, the agent does not call `apply_reviewed_proposal`
  at all; the conflict stays live and re-surfaces on the next recall. Reserve `defer` for
  explicit "not now / remind me later" intent; defer suppresses re-prompting, no-op does not.
- **Two-message clarification loop.** `both_valid` requires two non-empty scope labels.
  If the user names only one side's scope, the apply path returns `needs_clarification`
  (no write) and the agent re-prompts once: *"And [other claim] — when does that still
  hold?"* The complement default ("everywhere else / the default") maps to `context_scope=""`
  and is offered explicitly so the user need not invent a name.
- **Fork bead + single crawler pass.** A `both_valid` resolution writes a new fork-event
  bead that records the contextual disambiguation decision. The association crawler runs one
  pass on that fork bead immediately as part of the apply path, linking it to both origin
  beads. This satisfies the agent-judged invariant: the crawler pass is a direct consequence
  of the agent's explicit resolution decision, not auto-inference.

#### Schema changes

- `ClaimSchema` (and the persisted claim dict): add optional `context_scope: str | None`
  (defaults `None`; persisted as absent key for legacy records).
- `resolve_current_state()`: group by `(slot, context_scope or "")` instead of `slot`.
  Claims with identical `(slot, context_scope)` continue to conflict; claims with different
  `context_scope` values coexist. Legacy/global claims share the `""` bucket — fully
  backward-compatible.
- Epistemic scorer (`claim/epistemic.py`): skip cross-context pairs — different
  `context_scope` values on the same slot are not a conflict; `conflict_score_for_pair()`
  returns `0.0` when scopes differ.

#### Resolution apply path

When the agent maps the user's reply to `both_valid` and has both scope labels:

1. Validate: both `scope_a` and `scope_b` are non-empty strings. Return
   `needs_clarification` with an agent-readable `prompt` if either is missing.
2. Write two new context-scoped claims that supersede the two originals:
   - `{subject, slot, value: claim_a.value, context_scope: scope_a}` → supersedes `claim_a_id`
   - `{subject, slot, value: claim_b.value, context_scope: scope_b}` → supersedes `claim_b_id`
3. Emit a fork-event bead (turn text = the agent's resolution statement) via
   `process_turn_finalized`.
4. Call `emit_claim_updates` with the two supersede rows, using the fork bead as trigger.
5. Run one association crawler pass on the fork bead to link it to both origin beads
   (agent-judged: these links are a direct expression of the resolution decision).
6. Return `application_mode="context_scope_fork"` with `scope_a`, `scope_b`,
   `claim_updates_written: 2`, `fork_bead_id`.

#### Surface changes

- `resolution_to_claim_updates()` in `claim/conflict_review.py`: add `both_valid` branch.
- `build_conflict_review()`: add `both_valid` to `resolutions[]` with an `effect` string
  stating it requires two scope labels and describing the complement default.
- `agent_instructions` in `build_conflict_review()`: add explicit guidance — if the user
  picks `both_valid` but scopes only one side, ask once where the other still holds; offer
  "default / everywhere else" as an explicit answer; do *not* call `apply_reviewed_proposal`
  if the user refuses to scope either side.
- `decide_dreamer_candidate()` in `candidates.py`: add `both_valid` apply branch.
- `apply_reviewed_proposal` MCP tool: add `context_a: str | None` and `context_b: str | None`
  params (required when `resolution="both_valid"`).
- Agent guide (`core-memory-agent-guide.md`): document the two-message loop and complement
  default.

#### Tests

- `resolve_current_state()` coexistence: two claims for same slot, different `context_scope`
  → no conflict.
- `resolve_current_state()` backward compat: `context_scope=None` claims resolve as before.
- Epistemic scorer: cross-context pair returns `0.0`; same-context pair scores normally.
- `resolution_to_claim_updates()` `both_valid` branch produces two supersede rows with
  correct `context_scope` stamps.
- Apply path `needs_clarification` on missing scope label.
- Apply path full round-trip: conflict clears after `both_valid` resolution.
- "Both are just true, leave it" → agent skips `apply_reviewed_proposal` → conflict still
  live on next recall.
- Complement default (`scope_b=""`) coexists with `scope_a="prod"` without conflict.

---

### #12 — Dreamer: latent theme synthesis

**Status:** Complete  
**Blocked by:** nothing  
**Effort:** ~3 days  

**Shipped:**
- `synthesize_themes(root)` in `runtime/dreamer/analysis.py` — groups qualifying
  candidates (unreviewed, confidence ≥ 0.5, non-meta types) by (relationship, shared
  bead ID) via inverted index; emits `proposed_theme_candidate` for clusters of ≥ 3;
  myelination boost used as soft sort signal
- `proposed_theme` bead type in `schema/models.py` (`BeadType.PROPOSED_THEME`) and
  `schema/normalization.py` (`CANONICAL_BEAD_TYPES`)
- `enqueue_synthesized_themes(root, themes)` in `runtime/dreamer/candidates.py` —
  quarantine gate (< 3 `related_bead_ids` → skip); dedup by candidate key
- `_candidate_key()` extended: theme candidates key on `frozenset(related_bead_ids) +
  relationship` rather than source/target pair
- `decide_dreamer_candidate()` apply branch for `proposed_theme_candidate`: quarantine
  check at decision time; on accept+apply calls `process_turn_finalized()` with
  `metadata={"proposed_theme": {..., "type": "proposed_theme", "generated_by": "dreamer"}}`
- `side_effect_queue.py` dreamer-run job wires `synthesize_themes` + `enqueue_synthesized_themes`
  after `enqueue_dreamer_candidates()`; return dict includes `"theme_queue"`
- `eval.py` theme metrics: `theme_candidates`, `theme_decided`, `theme_accepted` counts;
  `theme_acceptance_rate` metric
- `tests/test_dreamer_theme_synthesis.py` — 22 tests, all passing

---

### #6 — Monotonic claim sequencing (`chain_seq`)

**Status:** Complete  
**Blocked by:** nothing (#0 is already closed)  
**Effort:** ~0.5 days

`chain_seq` is already defined on `ClaimUpdate` (`schema/models.py:468`) and populated
at write time via `_slot_highwater()` (`store_claim_ops.py:288-302`). The sort landed
as part of the #14 claim-resolution refactor.

**Shipped:**
- `resolve_current_state()` sorts `active_claims` by `chain_seq` before selecting the
  winner (`store_claim_ops.py:519`). Legacy records with `chain_seq: null` sort as 0 and
  degrade to list order — fully backward-compatible.
- `tests/test_store_claim_ops.py::TestStoreClaimOps::test_resolve_current_state_uses_chain_seq_for_supersede_winner`
  — two supersede updates arrive out of insertion order; asserts the higher-`chain_seq`
  claim wins.

---

## New architectural items

### #15 — Multi-store recall fan-out (External Memory Runtime)

**Status:** Spec complete; implementation not started  
**Blocks:** nothing  
**Blocked by:** #16 (PipeHouse adapter needs ingest contract first)  
**Effort:** ~4 days implementation  
**Spec:** `docs/PRD/multi-store-recall-fanout.md`

Fan-out `recall()` across Core Memory (causal/transcript), Ragie (multi-modal), and
PipeHouse (relational data insights). Per-store score normalization, unifying ID
grouping, degraded-mode handling. Ragie `ScoredChunk` fields confirmed from OpenAPI
spec: `id`, `score`, `text`, `document_metadata`, `links` (source URLs included in
retrieve response — no separate call needed). PipeHouse adapter is a placeholder
until #16 is complete.

---

### #16 — External data bead ingest contract

**Status:** Complete  
**Blocks:** #15 (PipeHouse adapter)  

**Shipped:**
- `BeadType.DATA_INSIGHT = "data_insight"` in `schema/models.py`; `"data_insight"` in
  `CANONICAL_BEAD_TYPES` in `schema/normalization.py`; NOT in `CLASSIFIABLE_TYPES` —
  always explicitly typed by the ingest path
- `runtime/ingest/` subpackage with `data_insight.py`:
  `ingest_data_insight_row(root, session_id, row)` validates required fields (raises
  `ValueError` on missing), builds a turn envelope, calls `emit_turn_finalized()` —
  never writes the bead store directly
  Turn ID `data-insight-{source_record_id}` ensures idempotency via Core Memory's
  existing turn deduplication
  `bead.links["external_source_id"]` + optional `core_memory_unifying_id` in links
- `"data-insight-poll"` job kind in `runtime/queue/side_effect_queue.py` and
  `runtime/queue/jobs.py` — reads up to `batch_size` uningested rows from
  `core_memory_insights`, calls `ingest_data_insight_row()` for each, sets
  `ingested_at = NOW()` on success; `CORE_MEMORY_PIPEHOUSE_DB_URL` env var gates the
  DB connection (no-op when unset)
- `POST /api/ingest/data-insight` endpoint in `demo/app.py` — validates body, calls
  `ingest_data_insight_row()`, returns `bead_id` or error
- `docs/schema/pipehouse_insights_table.sql` — SQL artifact for Chris (PipeHouse
  integration partner) with full column annotations and two indexes
- 17 tests in `tests/test_data_insight_ingest.py`, all passing

---

### #17 — Eval and benchmark layer

**Status:** Spec complete; implementation not started  
**Blocks:** nothing  
**Blocked by:** nothing  
**Effort:** ~3 days  
**Spec:** `docs/PRD/eval-benchmark-layer.md`

LoCoMo runner, baseline capture, CI smoke gate (20 queries on retrieval/ PRs),
nightly full run. Precision/recall/F1 per query type (causal, temporal, factual,
cross-session, contradiction). Each of #11, #13, #14, #15 ships with a committed
delta report. Works against `JsonFileBackend` only — zero external deps for CI.

---

## Dependency graph

```
#0 (window merge)      — CLOSED
#2 (goal lifecycle)    — CLOSED
#6 (claim sequencing)  — CLOSED

#11 (myelination wiring)  — CLOSED
├── #14 (contradiction pressure)  — CLOSED
│   └── #14A (both_valid + context_scope)  — CLOSED
└── #12 (dreamer themes)  — CLOSED

#16 (external bead ingest contract) — CLOSED
└── #15 (multi-store fan-out)  ← OPEN

#13 (temporal recall)   — CLOSED
#9B (enrichment delta)  — CLOSED
#10 (multi-speaker)     — CLOSED
└── #10A (N-speaker ingest gateway)  — CLOSED
    └── #10B (per-adapter source_system / MCP adapters)  — CLOSED
#17 (eval layer)        — CLOSED
```

## Open work

| Item | Status | Effort | Notes |
|------|--------|--------|-------|
| **#15** multi-store fan-out | Spec complete; implementation not started | ~4d | #16 closed; Ragie adapter spec confirmed |
