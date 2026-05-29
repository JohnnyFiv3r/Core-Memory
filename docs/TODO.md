# Core Memory — Canonical TODO

**Last updated:** 2026-05-29

Single source of truth for open capability work. Engine-correctness items #1–#9 are
closed — see `docs/status.md` for the record. This file covers the capability roadmap
(#10–#14), session enrichment Slice B (#9), and new architectural items (#15–#17).

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

`core_memory/runtime/goal_lifecycle.py` exists with outcome→goal detection:
- `_match_goal()` matches on shared tags or ≥2 shared tokens
- Bead type `"outcome"` triggers the resolution pass; `"goal"` beads are candidates
- Matched pairs produce a `resolves` association through the standard delta path
- Status transitions via `promotion_contract.py`

Confirmed closed in `docs/status.md` (2026-05-28).

---

## Open capability items (recommended build order)

### #9 Slice B — Session enrichment delta envelope

**Status:** Slice A done; Slice B pending  
**Blocks:** nothing; blocked by nothing  
**Effort:** ~3 days  
**Spec:** `docs/PRD/session-enrichment-delta-slice-b.md`

Implements idempotent enrichment run tracking: `enrichment_run_id` threading through
all 9 stages, atomic Stage 4 wrap, delta envelope persistence, and idempotency gate.
The delivery vehicle for association type expansion, entity fold, claims fold, goal
lifecycle fold, and semantic ergonomics — not new scope, just the envelope that makes
re-runs safe.

---

### #13 — Temporal recall API (`as_of`)

**Status:** Not started (data model complete; surface work only)  
**Blocks:** nothing; blocked by nothing  
**Effort:** ~2 days  
**Spec:** `docs/reports/capability-roadmap-prds.md` § #13

`resolve_all_current_state(root, session_id, as_of)` in `claim/resolver.py` already
resolves claims as of any timestamp. `recall()` has no `as_of` parameter so
"what did we believe about X on date Y?" is unanswerable through any public interface.

**Missing surface:**
- `as_of: str | None` parameter on `recall()` → retrieval planner → all tiers
- `RecallResult.metadata["as_of"]` and `EvidenceItem.metadata["created_at"]`
- CLI `core-memory recall --as-of`
- `POST /api/recall` body field `as_of`
- Input validation via `normalize_as_of()`; explicit error on bad input

**Note:** Semantic tier has no time axis — post-filter by `bead.created_at <= as_of`
after retrieval. Increase internal k by 1.5× when `as_of` is set to compensate.

---

### #11 — Myelination wiring

**Status:** Not started (primitives complete; wiring is the gap)  
**Blocks:** #14 (job infra), #12 (myelination signal)  
**Effort:** ~2 days  
**Spec:** `docs/reports/capability-roadmap-prds.md` § #11

`myelination.py` computes `bonus_by_edge_key` and `bonus_by_bead_id` from retrieval
telemetry. `retrieval_feedback.py` records every retrieval event. Neither is wired
into `retrieval_planner.py`. Scores are computed and discarded.

**Missing wiring:**
- `record_retrieval_feedback()` called at end of every `recall()` in `agent.py`
- `compute_myelination_bonus_map()` called in planner; bonuses applied to evidence
  scores before ranking (`new_score = min(1.0, max(0.0, base_score + bonus))`)
- Contradiction decay: `status: conflict` claim → decay signal on adjacent edges
- `"myelination-update"` job kind in `jobs.py`; caches result to `myelination-manifest.json`
- CLI `core-memory myelination report`

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

**Status:** Complete  
**Blocks:** the attribution queries #10 was built for ("what did Alice propose vs. what
Bob approved?")  
**Blocked by:** nothing (builds on #10)  
**Effort:** ~2 days

`normalize_transcript_payload()` is a **dyadic** front door: `_normalize_role()` requires
every utterance map to `user`/`assistant`, and the pairing loop walks user→assistant. The
runtime *below* it already supports N speakers (`runtime/state.py` carries
`speakers: list[str]`; `engine.py` notes multi-speaker rows "can legitimately have no
user/assistant role"). So a 5-person Slack thread or a meeting with `SPEAKER_00..04` is
collapsed to user/assistant pairs at the gateway — discarding structure the runtime
supports — *before* any identity logic runs. #10 resolves the identities correctly but
this gateway still throws away the participant structure.

**Missing:**
- A group/N-speaker ingest mode in `normalize_transcript_payload()` that accepts
  `participants` and per-utterance `speaker` without forcing the dyadic role collapse
- Preserve per-row `speaker` into the canonical envelope `turns[]` (already partially
  carried) so each participant resolves independently via `_resolve_envelope_speakers()`
- Keep the dyadic path intact for 1:1 agent chats (no regression)

---

### #10B — Per-adapter `source_system` convention (MCP ingest adapters)

**Status:** Complete  
**Blocked by:** #10A (multi-party gateway should land first)  
**Effort:** ~2 days per adapter; mechanical

`resolve_speaker()` takes `source_system` as an explicit parameter by design — core must
**not** sniff format. A Discord snowflake is just digits; Slack IDs, GitHub handles, and
raw `@display name` labels all look like plausible "labels," and guessing wrong silently
merges or splits identities (the worst failure mode for an identity layer). Per the adapter
law, the thing that knows the source is the adapter, not core.

**Policy:** *structured sources must use an adapter* — not *raw transcripts are banned*.
Raw transcript ingest degrades gracefully (generic normalization, still merges identical
labels); the only loss is ambiguous-case handling (`@user#1234` discriminator stripping,
positional `SPEAKER_00` scoping). Historical-import use cases without an MCP server yet
must remain possible.

**Missing:**
- Thin Slack / Discord / Zoom-Otter MCP adapters that emit the canonical envelope with
  `metadata.source_system` set and `turns[].speaker` populated, then call the existing
  ingest path unchanged
- Diarization-scope handling: the Zoom/Otter adapter scopes `source_system` with a
  recording id (e.g. `"zoom:rec-xyz"`) so `SPEAKER_00` from different recordings does not
  falsely merge to one entity
- No change to `transcript_ingest.py` internals — it already does the right thing given
  well-formed input

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

**Status:** Not started  
**Blocks:** nothing  
**Blocked by:** #14 (complete)  
**Effort:** ~2 days  

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

**Status:** Not started  
**Blocked by:** #11 (myelination signal), #14 (contradiction candidate type)  
**Effort:** ~3 days  
**Spec:** `docs/reports/capability-roadmap-prds.md` § #12

Dreamer runs pairwise structural analysis and produces association candidates.
The synthesis layer — identifying recurring motifs and higher-order abstractions
across the full memory graph — is missing. The system accumulates facts but does
not form abstractions.

**Critical constraint:** Dreamer never writes to the bead store directly. Every output
is `status: unreviewed` until explicitly accepted via `decide_dreamer_candidate()`.
This is non-negotiable and must be enforced structurally.

**Missing:**
- `synthesize_themes()` in `runtime/dreamer.py` — group candidates by shared bead
  cluster; emit `proposed_theme` when ≥ 3 candidates share a structural signal type
- `proposed_theme` bead type in schema
- `decide_dreamer_candidate()` apply branch for `proposed_theme`
- Quarantine: candidates with `len(related_bead_ids) < 3` rejected at enqueue
- Myelination hook: prefer clusters with positive `bonus_by_bead_id` mean (soft signal)

---

### #6 — Monotonic claim sequencing (`chain_seq`)

**Status:** Partial — field exists and is populated; resolution sort is the remaining fix  
**Blocked by:** nothing (#0 is already closed)  
**Effort:** ~0.5 days

`chain_seq` is already defined on `ClaimUpdate` (`schema/models.py:468`) and populated
at write time via `_slot_highwater()` (`store_claim_ops.py:288-302`). The remaining gap:
`resolve_current_state()` selects `active_claims[-1]` by list order (`store_claim_ops.py:508`)
without sorting by `chain_seq` first. Out-of-order async completions can produce
non-deterministic resolution.

**Fix (surgical — ~3 lines):**

In `resolve_current_state()`, before `active_claims[-1]`, sort by `chain_seq`:

```python
active_claims.sort(key=lambda c: int(c.get("chain_seq") or 0))
current = active_claims[-1] if active_claims else None
```

Legacy records with `chain_seq: null` sort as 0 — they degrade to list order, matching
current behavior. No schema changes required.

**Test:** Two claim updates for the same `(subject, slot)` arrive out of insertion order;
assert that `resolve_current_state()` returns the one with the higher `chain_seq`.

---

## New architectural items

### #15 — Multi-store recall fan-out (Satorid)

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

**Status:** Spec complete; implementation not started  
**Blocks:** #15 (PipeHouse adapter)  
**Effort:** ~2 days implementation  
**Spec:** `docs/PRD/external-data-bead-ingest.md`  
**PipeHouse table schema:** `docs/schema/pipehouse_insights_table.sql` (to be committed)

Adds `"data_insight"` bead type, `runtime/ingest/data_insight.py`, Mode A polling
job (`"data-insight-poll"`), and Mode B webhook (`POST /api/ingest/data-insight`).
Full bead schema, DB table contract, unifying ID convention, and association
eligibility are specified. Sends the SQL artifact to Chris as the build contract.

Bead schema, DB table contract (for Chris), ingest path (polling + webhook), unifying
ID convention (`core_memory_unifying_id` in `bead.links` and Ragie `document_metadata`),
and association eligibility are all fully specified. Includes a committed SQL artifact
at `docs/schema/pipehouse_insights_table.sql`.

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

#6 (claim sequencing)  — partial; surgical fix, no blockers

#11 (myelination wiring)
├── #14 (contradiction pressure)
│   ├── #14A (both_valid + context_scope)
│   └── #12 (dreamer themes)
└── #12 (dreamer themes)

#16 (external bead ingest contract)
└── #15 (multi-store fan-out)

#13 (temporal recall)   — no dependencies
#9B (enrichment delta)  — no dependencies
#10 (multi-speaker)     — complete
└── #10A (N-speaker ingest gateway)
    └── #10B (per-adapter source_system / MCP adapters)
#17 (eval layer)        — no dependencies
```

## Recommended build sequence

| Step | Item | Effort | Rationale |
|------|------|--------|-----------|
| 1 | **#6** chain_seq sort fix | 0.5d | Surgical; unblocks correct claim resolution |
| 2 | **#13** temporal recall | 2d | No deps; high value, quick win |
| 3 | **#11** myelination | 2d | No deps; unblocks #14 and #12 |
| 4 | **#9B** enrichment delta | 3d | No deps; makes re-runs safe |
| 5 | **#16** ingest impl | 2d | Spec done; unblocks #15 PipeHouse adapter |
| 6 | **#10** multi-speaker | 4d | Complete |
| 7 | **#14** contradiction pressure | 3d | After #11; complete |
| 8 | **#14A** both_valid + context_scope | 2d | After #14; extends resolution vocabulary |
| 9 | **#17** eval layer | 3d | Parallel to any of the above |
| 10 | **#15** multi-store fan-out | 4d | After #16; Ragie adapter spec confirmed |
| 11 | **#12** dreamer themes | 3d | After #11 + #14 |
| 12 | **#10A** N-speaker ingest gateway | 2d | Unlocks the attribution queries #10 was built for |
| 13 | **#10B** per-adapter source_system / MCP adapters | 2d/adapter | After #10A; structured sources via adapter, raw ingest stays |
