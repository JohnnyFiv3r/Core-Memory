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

**Status:** Not started (research phase required first)  
**Blocks:** nothing; blocked by nothing  
**Effort:** ~1 day research + ~3 days implementation  
**Spec:** `docs/reports/capability-roadmap-prds.md` § #10

`transcript_ingest.py` records `user_speaker`/`assistant_speaker` as opaque strings.
No identity resolution exists. Causal chains break at participant boundaries — "we
decided to drop Kubernetes" is unattributable when there is no mechanism to link
`johnnyfiv3r` (Discord), `johnny` (Slack), and `jf@company.com` (email) to a single
entity across sessions.

**Research phase (before any schema locks):** document Discord, Slack, Zoom/Otter, and
GitHub label representations. Produce `docs/plans/speaker-schema-research.md`.

**Missing implementation:**
- `entity/speaker_resolver.py` — `resolve_speaker(index, observed_label, source_system)`
- Wire resolver into `transcript_ingest.py` before `process_turn_finalized()`
- `SpeakerAttribution` dataclass: `speaker_observed`, `resolved_entity_id`,
  `resolution_confidence`, `source_system`, `aliases`
- `attributed_entity_id` + `resolution_confidence` on bead provenance
- `register_speaker_alias()` on `entity/registry.py`
- `SPEAKER_RESOLUTION_CONFIDENCE_THRESHOLD` env var, default 0.75

---

### #14 — Contradiction pressure and epistemic uncertainty

**Status:** Not started  
**Blocks:** #12 (dreamer candidate type)  
**Blocked by:** #11 (myelination job infra)  
**Effort:** ~3 days  
**Spec:** `docs/reports/capability-roadmap-prds.md` § #14

`store_claim_ops.py` marks claims `status: conflict` when two claims share
`(subject, slot)` with no supersede. That conflict state never propagates — not to
associations, not to `recall()` results, not to the user. Ambiguity is silently
swallowed.

**Missing:**
- `claim/epistemic.py` — `compute_epistemic_conflict_score()` → float [0.0, 1.0]
- `ConflictItem` dataclass + `RecallResult.conflicts` in `retrieval/contracts.py`
- Retrieval planner populates `conflicts` and `uncertainty_pressure` on associations
- `dreamer_candidates.py` — `contradiction_pressure_candidate` type; emit when
  score > `CORE_MEMORY_CONFLICT_REVIEW_THRESHOLD` (default 0.7)

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
│   └── #12 (dreamer themes)
└── #12 (dreamer themes)

#16 (external bead ingest contract)
└── #15 (multi-store fan-out)

#13 (temporal recall)   — no dependencies
#9B (enrichment delta)  — no dependencies
#10 (multi-speaker)     — no dependencies
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
| 6 | **#10** multi-speaker | 4d | Research first; foundational for multi-participant |
| 7 | **#14** contradiction pressure | 3d | After #11 |
| 8 | **#17** eval layer | 3d | Parallel to any of the above |
| 9 | **#15** multi-store fan-out | 4d | After #16; Ragie adapter spec confirmed |
| 10 | **#12** dreamer themes | 3d | After #11 + #14 |
