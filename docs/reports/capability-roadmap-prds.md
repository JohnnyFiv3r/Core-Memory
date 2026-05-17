# Capability Roadmap — PRD-lite Plans

**Date:** 2026-05-17  
**Scope:** Items #10–14 from `demo/TODO.md`, ordered by priority  
**Format:** Each PRD is self-contained. Implementation tasks assume a clean branch per item.

---

## #13 — Temporal State Recall API

**Priority:** Immediate (data model complete; this is pure surface work)

### Problem

`resolve_all_current_state(root, session_id, as_of)` in `claim/resolver.py` already resolves claims as of any timestamp. `recall()` has no `as_of` parameter, so "what database were we using in March?" is unanswerable through any public interface — the temporal data model is invisible to users.

### User value

- Audit and compliance queries: "what did we believe about X on date Y?"
- Project history diffs: "show me decision state at the start of Q2 vs end of Q3"
- Debugging: "the recall result changed — what changed between these two timestamps?"

### Current state

| Component | Status |
|-----------|--------|
| `temporal.py`: `claim_visible_as_of()`, `update_visible_as_of()`, `normalize_as_of()` | **Done** |
| `claim/resolver.py`: `resolve_all_current_state(root, session_id, as_of)` | **Done** |
| `retrieval/agent.py`: `recall()` accepts `as_of` | **Missing** |
| `retrieval/contracts.py`: `RecallResult` carries `as_of` + annotated evidence | **Missing** |
| `retrieval/retrieval_planner.py`: passes `as_of` to all tiers | **Missing** |
| CLI `core-memory recall --as-of` | **Missing** |
| Demo `POST /api/recall` with `as_of` body field | **Missing** |

### Success criteria

1. `recall("what db were we using", effort="high", as_of="2026-03-01")` returns evidence visible before that date and excludes beads created after it.
2. `RecallResult.metadata["as_of"]` is present and matches the input.
3. Each `EvidenceItem` carries `created_at` in its `metadata` so callers can see where items sit relative to the boundary.
4. Misformatted or future `as_of` values return an explicit error, not a silent empty result.
5. `claim/resolver.py` is the sole temporal authority — no ad-hoc timestamp filtering elsewhere.

### Scope

**In:**
- `as_of` parameter on `recall()`, retrieval planner, semantic/lexical/claim tiers
- `RecallResult.metadata["as_of"]` and `EvidenceItem.metadata["created_at"]`
- CLI `--as-of` flag on `core-memory recall`
- Demo `POST /api/recall` body field `as_of`
- Input validation: parse via `normalize_as_of()`, raise `ValueError` on failure

**Out:**
- New temporal data model — use `core_memory/temporal.py` only
- Write-time temporal constraints
- Per-tier `as_of` overrides (single boundary applies everywhere)
- Historical semantic index snapshots (the vector index is not time-sliced)

### Implementation tasks

1. **`retrieval/agent.py`** — Add `as_of: str | None = None` to `recall()` signature. Pass it through the `request` dict to `memory_execute`. Add `normalize_as_of(as_of)` call at entry; raise on bad input.

2. **`retrieval/retrieval_planner.py`** — Accept `as_of` from the request dict. Pass it to claim resolution calls and to bead/evidence filter helpers. Semantic and lexical tiers filter `EvidenceItem` results by `bead.created_at <= as_of` after retrieval (vector index has no time axis — post-filter is sufficient and correct).

3. **`retrieval/contracts.py`** — Add `as_of: str | None` to `RecallResult` (default `None`). Populate in `recall_result_from_memory_execute()`. Add `created_at` to `EvidenceItem.metadata` in `evidence_from_result_row()`.

4. **`claim/resolver.py`** — No changes required. `resolve_all_current_state(root, as_of=...)` already works; confirm the planner passes `as_of` correctly.

5. **`cli.py`** — Add `--as-of` argument to the `recall` subparser. Pass to `recall()`.

6. **`demo/app.py`** — Extract `as_of` from POST body in `/api/recall`. Pass to `recall()`. Include `as_of` in response metadata.

7. **Tests** — Two fixtures: one verifying a bead created after `as_of` is excluded; one verifying a bad `as_of` string returns a clear error.

### Dependencies / risks

- Semantic tier post-filtering means vector recall may return k results that are then filtered to fewer. Adjust: increase internal k when `as_of` is set to compensate for expected post-filter shrinkage (multiply by 1.5, capped at max k).
- Retrieval planner must apply the boundary **before** returning evidence to the caller — not after. Avoid a pattern where the evidence list is assembled and then trimmed, because tier scoring depends on the evidence set.

---

## #10 — Multi-Speaker Attribution and Identity Persistence

**Priority:** Immediate (research phase first; foundational for multi-participant memory)

### Problem

`transcript_ingest.py` records `user_speaker` and `assistant_speaker` as opaque metadata strings. No identity resolution exists. "We decided to drop Kubernetes" is unattributable when there is no mechanism to link `johnnyfiv3r` (Discord), `johnny` (Slack), and `jf@company.com` (email) to a single entity across sessions.

### User value

- Attribution queries: "what did Alice propose vs what Bob approved?"
- Organization-level memory: causal chains that span participants, not just sessions
- Group agent transcripts: Slack threads, meeting notes, GitHub discussions

### Research requirement (before any schema locks)

Study at least three target transcript formats before finalizing the schema:
- Discord: snowflake user IDs + display names that change
- Slack: workspace-scoped user IDs + display names
- Meeting transcripts (Zoom, Otter): speaker diarization labels (SPEAKER_00, SPEAKER_01)
- GitHub discussions: @mention login handles

The schema must embrace identity uncertainty as a first-class constraint.

### Current state

| Component | Status |
|-----------|--------|
| `transcript_ingest.py`: records `user_speaker`, `assistant_speaker` in metadata | **Done** |
| `entity/registry.py`: alias normalization, entity ID generation, alias lookup | **Done** |
| Multi-speaker turn schema (per transcript) | **Done** |
| `entity/speaker_resolver.py` | **Does not exist** |
| Speaker → entity resolution in ingest path | **Missing** |
| Claims attributable to `resolved_entity_id` | **Missing** |
| Cross-session speaker persistence | **Missing** |

### Success criteria

1. A transcript with `speaker: "johnnyfiv3r"` ingested twice (different sessions) resolves to the same `entity_id` in the entity registry.
2. `resolution_confidence` is present on every resolution and is inspectable via `core-memory entity show <id>`.
3. A claim from a resolved speaker carries `attributed_entity_id` in its provenance.
4. Two different observed labels that alias to the same entity (e.g., `@john` and `johnnyfiv3r`) merge correctly at confidence ≥ 0.88 (the threshold already used by `dreamer_candidates.py`).
5. A speaker that cannot be resolved at high confidence is stored as `resolution_confidence < threshold`, not silently dropped or falsely merged.

### Scope

**In:**
- `entity/speaker_resolver.py` — new module; resolution via `entity/registry.py` (no new entity store)
- Ingest path: wire speaker labels through the resolver before `process_turn_finalized()`
- `resolution_confidence` field on resolved attribution
- Attribution provenance on claims (`attributed_entity_id`, `resolution_confidence`)
- Entity registry alias path: speaker observed labels as aliases

**Out:**
- Building a new identity store — use the existing entity registry
- LLM-based identity disambiguation in the first cut (heuristic alias matching only; LLM escalation is an optional later addition)
- Authoritative identity federation (OAuth, user accounts)
- Retroactive re-attribution of existing beads

### Implementation tasks

1. **Research phase** — Read at minimum Discord, Slack, and meeting transcript export formats. Document the label representations and produce a field-mapping table before writing schema. Output: a committed artifact `docs/plans/speaker-schema-research.md`.

2. **`entity/speaker_resolver.py`** — New module. Public function: `resolve_speaker(index, observed_label, source_system) → SpeakerResolution`. Logic:
   - Normalize observed label via `normalize_entity_alias()`
   - Call `_find_entity_id(index, normalized)` from `registry.py`
   - If found: return with `resolution_confidence` from alias match quality
   - If not found: create new entity via `_new_entity_id()`, register alias, return at confidence 1.0 (new entity)
   - Threshold: confidence < 0.6 → store as unresolved observation (do not create false merge)

3. **`transcript_ingest.py`** — After normalization, before `ingest_turn_envelopes()`, resolve each unique speaker label. Attach `resolved_entity_id` and `resolution_confidence` to the turn envelope's metadata.

4. **`schema/models.py`** (or equivalent speaker attribution fields) — Add `SpeakerAttribution` dataclass: `speaker_observed`, `resolved_entity_id`, `resolution_confidence`, `source_system`, `aliases`.

5. **`persistence/store_add_bead_ops.py`** — When writing a bead from a resolved-speaker turn, attach `attributed_entity_id` and `resolution_confidence` to the bead's provenance block.

6. **`entity/registry.py`** — Add `register_speaker_alias(index, entity_id, observed_label, source_system)` — thin wrapper that normalizes and upserts an alias with source provenance.

7. **Tests** — Three fixtures: (a) same speaker label in two sessions resolves to same entity ID; (b) two different labels that normalize identically merge; (c) low-confidence label is stored as unresolved without creating a spurious entity.

### Dependencies / risks

- `_find_entity_id` is a partial function in the first 100 lines read — verify the full lookup logic before building on it.
- The alias-match confidence threshold (0.88 from `dreamer_candidates.py`) may be too high for speaker labels where usernames share prefixes. Use a separate, tunable `SPEAKER_RESOLUTION_CONFIDENCE_THRESHOLD` env var, defaulting to 0.75.
- Retroactive attribution: beads ingested before this feature have no `attributed_entity_id`. Any attribution query must handle missing attribution gracefully (not treat it as "unattributed to anyone").

---

## #11 — Myelination: Retrieval-Routing Reinforcement

**Priority:** Immediate (primitives in place; wiring is the gap)

### Problem

`myelination.py` computes `bonus_by_edge_key` and `bonus_by_bead_id` from retrieval feedback telemetry. `retrieval_feedback.py` records every retrieval event with edges and outcomes. Neither is wired into `retrieval_planner.py`. The learning loop exists but is disconnected — scores are computed and discarded.

### User value

- Recall quality improves over time without manual curation
- Frequently useful retrieval paths are preferred in ranking
- Contradicted or never-useful paths are de-prioritized (not hidden)
- Multi-hop paths that consistently resolve queries become "cognitive highways"

### Current state

| Component | Status |
|-----------|--------|
| `myelination.py`: `compute_myelination_bonus_map(root, since, limit)` | **Done** |
| `retrieval_feedback.py`: `record_retrieval_feedback()`, `read_retrieval_feedback()` | **Done** |
| `myelination.py`: `myelination_report()` | **Done** |
| Bonus map applied in `retrieval_planner.py` | **Missing** |
| Feedback recording wired after `recall()` returns | **Missing** |
| Contradiction pressure → decay signal | **Missing** |
| Async job for myelination score update | **Missing** |

### Success criteria

1. After 10 successful recall events touching the same edge, that edge's `bonus_by_edge_key` is positive and observable via `myelination_report()`.
2. `retrieval_planner.py` applies `bonus_by_bead_id` as a score adjustment to evidence ranking — the top result changes when myelination bonus is nonzero.
3. A decayed edge is still returned in results — it is re-ranked, not suppressed.
4. Feedback is recorded after every `recall()` call (success or failure) — not only on success.
5. `core-memory myelination report` returns a parseable JSON summary.

### Scope

**In:**
- Wire `record_retrieval_feedback()` call at the end of `recall()` in `agent.py`
- Wire `compute_myelination_bonus_map()` into `retrieval_planner.py` — apply bonuses to evidence scores before ranking
- Contradiction pressure decay: when `resolve_current_state()` returns `status: conflict` for a claim, apply a decay signal to edges whose source/target beads carry that claim
- `jobs.py` — add a `myelination-update` job kind that runs `compute_myelination_bonus_map()` and caches result to a lightweight manifest file
- CLI `core-memory myelination report` wrapping `myelination_report()`

**Out:**
- Multi-hop path tracking in the first cut (single-edge bonuses first; path bonuses are additive later)
- Applying myelination to entity alias resolution
- Myelination on claim slots (associations only in this slice)
- Changing the feedback storage format (use `retrieval-feedback.jsonl` as-is)

### Implementation tasks

1. **`retrieval/agent.py`** — After `memory_execute` returns, call `record_retrieval_feedback(root, request=..., response=raw, source="recall")`. The `answer_outcome` field in the response already determines success/failure — no additional classification needed.

2. **`retrieval/retrieval_planner.py`** — At plan execution time, call `compute_myelination_bonus_map(root)` (cached; stale for up to 5 min acceptable). For each `EvidenceItem` in the result set, look up `bonus_by_bead_id.get(bead_id, 0.0)` and add it to the item's score before final ranking. Cap the adjustment: `new_score = min(1.0, max(0.0, base_score + bonus))`.

3. **`runtime/myelination.py`** — Add `apply_contradiction_decay(root, bonus_map)` function: scan `resolve_current_state()` results for `status: conflict`; for each conflicted claim's bead, apply `neg_cap` decay to that bead's bonus. Called from the myelination update job, not inline.

4. **`runtime/jobs.py`** — Add `"myelination-update"` job kind. The job calls `compute_myelination_bonus_map()` and writes a `myelination-manifest.json` to `.beads/events/`. Planner reads from the manifest instead of recomputing on every request.

5. **`cli.py`** — Add `myelination` subcommand group with a `report` subcommand. JSON output from `myelination_report()`.

6. **Tests** — Two fixtures: (a) a retrieval event recorded → bonus map shows nonzero edge bonus after compute; (b) evidence ranking changes when a nonzero bonus is applied.

### Dependencies / risks

- `compute_myelination_bonus_map()` requires sufficient feedback history — minimum 2 hits per edge (`CORE_MEMORY_MYELINATION_MIN_HITS`). In a fresh deployment with few recalls, all bonuses are 0.0 and ranking is unchanged. This is correct behavior, not a bug.
- The 5-minute stale tolerance on the cached manifest is acceptable for now. If hot-path latency is a concern, precompute on a background timer instead.
- `memory_execute` may not return structured edge provenance in all code paths — verify `_collect_edges()` in `retrieval_feedback.py` can extract edges from the planner's response before assuming feedback recording works end-to-end.

---

## #14 — Contradiction Pressure and Epistemic Uncertainty

**Priority:** Next layer (builds on claim conflict machinery; moderate complexity)

### Problem

`store_claim_ops.py` marks claims as `status: conflict` when two claims share the same `(subject, slot)` with no supersede relationship. That conflict state never propagates outward — not to associations whose evidence depends on contested claims, not to `recall()` results, not to the user. Ambiguity is silently swallowed.

### User value

- Honest recall: "the answer to your question is contested — here is both sides"
- Contradiction surfacing: "this claim has been in conflict for 6 months with no resolution"
- Trust calibration: knowing when Core Memory is confident vs. genuinely uncertain

### Current state

| Component | Status |
|-----------|--------|
| `store_claim_ops.py`: `resolve_current_state()` returns `conflicts` list and `status: conflict` | **Done** |
| `contracts.py`: `RecallResult` has no `conflicts` field | **Missing** |
| `EvidenceItem`: no epistemic uncertainty annotation | **Missing** |
| Association `uncertainty_pressure` field | **Missing** |
| `epistemic_conflict_score` computation | **Missing** |
| Conflict surfacing in `recall()` | **Missing** |
| Human review routing for high-pressure conflicts | **Missing** |

### Success criteria

1. When a `recall()` query touches a subject+slot with `status: conflict`, `RecallResult.conflicts` contains the conflicting claims with their `epistemic_conflict_score`.
2. `epistemic_conflict_score` is in [0.0, 1.0]: a conflict that spans a long time window with no supersede scores higher than a same-session conflict that was immediately resolved.
3. Associations whose source or target bead carries a conflict score > 0.5 have `uncertainty_pressure` in their metadata.
4. Conflicted claims remain fully queryable — `recall()` still returns the best available evidence in `evidence`; the `conflicts` field is additive information.
5. When `epistemic_conflict_score` > configurable threshold (default 0.7), a conflict candidate is written to `dreamer-candidates.json` for user review.

### Scope

**In:**
- `claim/epistemic.py` — new module; `compute_epistemic_conflict_score(claim_a, claim_b, chain_seq_gap, time_delta_days)` → float
- `RecallResult.conflicts: list[ConflictItem]` field (new dataclass in `contracts.py`)
- `retrieval/retrieval_planner.py`: resolve claim slots for query subject; populate `conflicts` in result
- Association `uncertainty_pressure` metadata: write when conflict score > 0.5
- `dreamer_candidates.py`: add `contradiction_pressure_candidate` type; emit when score > threshold

**Out:**
- Auto-resolution of conflicts — the system raises; the user resolves
- Semantic similarity as a conflict detection mechanism — conflicts come from the claim graph only
- Changing bead content based on conflict score
- Suppressing or hiding conflicted claims from results

### Implementation tasks

1. **`claim/epistemic.py`** — New module. `compute_epistemic_conflict_score(claim_a, claim_b, chain_seq_gap: int, time_delta_days: float) → float`:
   - `time_component = min(1.0, time_delta_days / 180.0)` — conflict lasting 6+ months scores 1.0
   - `seq_component = 0.0 if chain_seq_gap == 0 else min(1.0, chain_seq_gap / 10.0)` — large seq gap (unresolved supersede) scores higher
   - `score = 0.6 * time_component + 0.4 * seq_component`
   - Returns float clamped to [0.0, 1.0]

2. **`retrieval/contracts.py`** — Add `ConflictItem` dataclass: `subject`, `slot`, `claim_a_id`, `claim_b_id`, `epistemic_conflict_score`, `conflict_since`, `chain_seq_gap`. Add `conflicts: list[ConflictItem] = field(default_factory=list)` to `RecallResult`.

3. **`retrieval/retrieval_planner.py`** — After claim resolution, for each slot with `status: conflict`, call `compute_epistemic_conflict_score()` on the conflicting pair. Populate `RecallResult.conflicts`. Pass `uncertainty_pressure` into the association metadata for associations whose bead endpoints carry active conflicts.

4. **`claim/resolver.py`** — `resolve_all_current_state()` already returns `conflict_slots`. Add `chain_seq_gap` computation: `max(chain_seq in updates) - min(chain_seq in updates)` for conflicting updates on a slot.

5. **`dreamer_candidates.py`** — In `enqueue_dreamer_candidates()`, add `contradiction_pressure_candidate` type. Emit when `epistemic_conflict_score` > `CORE_MEMORY_CONFLICT_REVIEW_THRESHOLD` (env var, default 0.7). Reuse existing `decide_dreamer_candidate()` flow for user review.

6. **`persistence/store_claim_ops.py`** — No schema changes. `resolve_current_state()` already returns enough data; `epistemic.py` consumes it.

7. **Tests** — Three fixtures: (a) two conflicting claims produce nonzero `epistemic_conflict_score`; (b) `recall()` result includes `conflicts` when relevant; (c) score above threshold triggers a dreamer candidate.

### Dependencies / risks

- Populating `conflicts` in the planner requires knowing the query's subject — not always available from a freetext query. Heuristic: resolve conflicts for all claim subjects that appear in the returned evidence beads. This is correct and bounded.
- Association `uncertainty_pressure` requires knowing which associations point to conflicted beads. This is a graph traversal — scope it to one hop (direct source/target only) in the first cut.

---

## #12 — Dreamer: Latent Theme Synthesis

**Priority:** Next layer (requires myelination signals; stretch goal)

### Problem

`dreamer.py` runs structural recombination analysis and produces high-quality association candidates. `dreamer_candidates.py` handles the candidate queue with accept/reject/apply flows. `dreamer_eval.py` measures quality. The synthesis layer — identifying recurring motifs and higher-order abstractions across the full memory graph — is the missing piece. The system accumulates facts but does not form abstractions.

### User value

- Organization memory: "our Q1–Q3 decisions all reduced operational surface area"
- Pattern recognition: detecting recurring failure modes, successful strategies, latent tensions
- Stretch: the system raises a candidate theme; the user confirms it; it becomes a navigable memory node

### Current state

| Component | Status |
|-----------|--------|
| `dreamer.py`: pairwise structural analysis, novelty/grounding/confidence scores | **Done** |
| `dreamer_candidates.py`: full queue with accept/reject, entity merge, retrieval value override | **Done** |
| `dreamer_eval.py`: evaluation metrics (accepted rate, downstream use, cross-session transfer) | **Done** |
| `proposed_theme` provisional bead type | **Missing** |
| Synthesis pass that consumes multiple candidates and forms an abstraction | **Missing** |
| Myelination-guided candidate prioritization | **Missing** (depends on #11) |
| User-facing surface to review and accept/reject theme candidates | **Missing** |

### Critical constraint

Every Dreamer output is `status: unreviewed` until explicitly accepted. Dreamer never writes to the bead store directly. This is non-negotiable and must be enforced structurally (not by convention).

### Success criteria

1. `dreamer.py` can emit a `proposed_theme` candidate that cites ≥ 3 `related_bead_ids` from the actual graph.
2. A candidate with no grounded `related_bead_ids` is quarantined — not enqueued.
3. `decide_dreamer_candidate(candidate_id, decision="accept", apply=True)` on a `proposed_theme` creates a bead with `type: proposed_theme`, `status: accepted`, and the cited bead IDs in its provenance.
4. Rejected candidates are archived with the rejection reason.
5. `dreamer_eval.py` metrics include `proposed_theme` acceptance rate.

### Scope

**In:**
- `proposed_theme` bead type added to the bead type schema
- `dreamer.py` synthesis pass: group association candidates by shared bead clusters; when ≥ 3 related candidates share a structural signal, emit a `proposed_theme` candidate
- `dreamer_candidates.py`: handle `proposed_theme` type in `decide_dreamer_candidate()` — on accept+apply, write a provisional bead via `process_turn_finalized()` with `type: proposed_theme`
- Quarantine: candidates with zero grounded `related_bead_ids` are rejected at enqueue time with `"no_grounded_evidence"` reason
- Myelination hook: prefer synthesis of bead clusters where edges have high myelination bonus (use `bonus_by_bead_id` from the myelination manifest as a signal, not a gate)

**Out:**
- Dreamer writing to the bead store directly — it always goes through `decide_dreamer_candidate(apply=True)` with user approval
- Dreamer reasoning over raw turn text — synthesis runs on committed beads and associations only
- LLM-generated themes without grounded bead citations
- Automated acceptance of Dreamer candidates

### Implementation tasks

1. **`runtime/dreamer.py`** — Add `synthesize_themes(beads_dir_or_store, candidates)` function. Logic:
   - Group `dreamer_candidates.json` entries (pending, score ≥ 0.5) by shared `source_bead_id` or `target_bead_id`
   - A cluster of ≥ 3 candidates that share a structural signal type (e.g., `transferable_lesson`, `structural_symmetry`) is a candidate theme
   - Produce one `proposed_theme` candidate per cluster: `related_bead_ids = [all source/target bead IDs in cluster]`, `confidence = mean(candidate scores)`, `because = "N candidates share {signal_type} across {N sessions}"` (grounded, not invented)
   - Quarantine: skip any candidate with `len(related_bead_ids) < 3`

2. **`runtime/dreamer_candidates.py`** — Add `proposed_theme` to `_hypothesis_type()` and `_proposal_family()` mappings. In `decide_dreamer_candidate()`, handle `proposed_theme` type in the apply branch: call `process_turn_finalized()` with a synthetic turn containing the theme bead. Add quarantine check in `enqueue_dreamer_candidates()`: if `related_bead_ids` is empty, log and skip.

3. **Schema** — Add `proposed_theme` to the bead type enum. Attributes: `confidence`, `generated_by: "dreamer"`, `related_bead_ids`, `status: "unreviewed" | "accepted" | "rejected"`, `because`.

4. **`runtime/jobs.py`** — Wire `synthesize_themes()` into the Dreamer cron job, after `run_analysis()` completes and its candidates are enqueued.

5. **`runtime/dreamer_eval.py`** — Add `proposed_theme` to the categorization and metric computation. Add `theme_acceptance_rate` metric.

6. **Myelination hook** — In `synthesize_themes()`, load the myelination manifest (`myelination-manifest.json`). Prefer clusters where the mean `bonus_by_bead_id` across cluster members is positive. This is a soft priority signal only — do not suppress low-myelination clusters entirely.

7. **Tests** — Three fixtures: (a) a cluster of ≥ 3 candidates produces a `proposed_theme` candidate; (b) a candidate with no grounded bead IDs is quarantined; (c) accept+apply on a `proposed_theme` creates a bead with the correct type and provenance.

### Dependencies / risks

- **Depends on #11 (myelination)** for the prioritization hook. The synthesis pass works without it — the myelination hook degrades gracefully to equal priority if the manifest is absent.
- Dreamer runs pairwise on up to 20×25 bead combinations. On large graphs, `synthesize_themes()` must not re-run pairwise analysis — it consumes the already-scored candidates file, not raw beads.
- `proposed_theme` beads are provisional and must not appear in default `recall()` results with `status: unreviewed`. Filter them unless the caller explicitly passes `include_provisional=True`.

---

## Cross-cutting notes

**Order of precedence for unblocked work:**
1. **#13** — No dependencies. Start immediately. One engineer, ~2 days.
2. **#10** — Research phase first (1 day). Implementation ~3 days after schema is settled.
3. **#11** — No dependencies on #10 or #13. Parallel to either. ~2 days.
4. **#14** — Start after #11 wiring is done (uses myelination job infrastructure). ~3 days.
5. **#12** — Start after #11 (myelination hook) and #14 (contradiction candidate type in `dreamer_candidates.py`). ~3 days.

**Shared infrastructure touched by multiple items:**
- `retrieval/contracts.py` — #13 adds `as_of` + `EvidenceItem.created_at`; #14 adds `ConflictItem` + `RecallResult.conflicts`. Coordinate on schema version bump.
- `retrieval/retrieval_planner.py` — #13 adds temporal filtering, #11 adds bonus application, #14 adds conflict resolution. Coordinate: these are additive passes in sequence, not competing changes.
- `dreamer_candidates.py` — #14 adds `contradiction_pressure_candidate`; #12 adds `proposed_theme`. Both are new hypothesis types; they compose cleanly.
- `jobs.py` — #11 adds `myelination-update`; #12 adds to the Dreamer cron. These are independent job kinds.
