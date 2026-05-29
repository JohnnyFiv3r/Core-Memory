# Core Memory — Canonical TODO

**Last updated:** 2026-05-29

Single source of truth for open capability work. Engine-correctness items #1–#9 are
closed — see `docs/status.md` for the record. This file covers #0 (new prerequisite),
the capability roadmap (#10–#14), session enrichment Slice B (#9), and new architectural
items (#15–#17).

Detailed PRDs for #10–#14: `docs/reports/capability-roadmap-prds.md`
Validation snapshot (2026-05-15): `docs/reports/todo-validation-2026-05-15.md`

---

## Prerequisite — must land before #2 and #6

### #0 — `visible_bead_ids ∪ window_bead_ids` merge

**Status:** Not started  
**Blocks:** #2 (goal lifecycle), #6 (claim sequencing)  
**Effort:** ~1 day

**Problem:** `emit_claim_updates()` compares new claims against `visible_bead_ids`,
which is populated from the current session only. `window_bead_ids` (recalled
cross-session beads) is already threaded through `ingress.py`, `turn_flow.py`, and
`enrichment.py` but is never merged into the visible set before the claim decision
pass runs. A supersede or goal resolution in session N therefore cannot find or act
on a claim from session 1, even when that bead was recalled into context. The write
machinery believes it has full visibility; it does not.

**Fix:** Before `emit_claim_updates()` executes, compute
`effective_visible = visible_bead_ids | window_bead_ids` and pass that as the visible
set for the claim decision pass. This is the only change. Do not silently expand scope
anywhere else — cross-session visibility must remain explicit and opt-in.

**Constraints:**
- The merge must happen in `turn_flow.py` or `enrichment.py` at the call site of
  `emit_claim_updates()`, not inside the function itself.
- Do not alter the default scope of any other pass. Only the claim decision pass
  receives the expanded set.
- Add one test: a supersede issued in session 2 correctly finds and retires a
  conflicting claim written in session 1.

---

## Open capability items (recommended build order)

### #9 Slice B — Session enrichment delta envelope

**Status:** Slice A done; Slice B pending  
**Blocks:** nothing; blocked by nothing  
**Effort:** ~3 days  
**Spec:** `docs/PRD/execution-plan-search-quality-and-enrichment.md`,
`docs/contracts/session_enrichment_delta_v1.md`

Implements delta envelope phases 5–9: association type expansion, entity registry
fold, claims + sequencing fold, goal lifecycle fold, semantic indexing ergonomics.
These map 1:1 to #3, #6, #2, #7 in the original engine list — the delta envelope
is the delivery vehicle, not new scope.

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

### #2 — Goal lifecycle resolution

**Status:** Not started  
**Blocked by:** #0 (window merge prerequisite)  
**Effort:** ~3 days  
**Spec:** `docs/reports/capability-roadmap-prds.md` (implicit in session enrichment delta)

A dedicated resolution pass runs after each turn's enrichment and asks: does this
turn's outcome bead relate to any open `candidate` goal bead? No detection mechanism,
outcome→goal association path, or status transition machinery exists anywhere in the
codebase.

**Constraints:**
- Resolution produces a `resolves` or `outcome_of` association through the standard
  delta path — not a bespoke write.
- Goal status transitions `candidate → resolved` via existing promotion machinery
  (`promotion_contract.py`), not a new state machine.
- The detection mechanism lives in `core_memory/runtime/` or `core_memory/policy/`,
  never in OpenClaw or any plugin bridge.
- Must not silently expand the visible window. Requires #0 to be in place first.

---

### #6 — Monotonic claim sequencing (`chain_seq`)

**Status:** Not started  
**Blocked by:** #0 (window merge prerequisite)  
**Effort:** ~2 days

`store_claim_ops.py:329` picks `active_claims[-1]` by list order to resolve supersede
chains. No `chain_seq` counter exists. Async out-of-order job completion produces
non-deterministic resolution.

**Fix:**
- `chain_seq` integer counter maintained per `(subject, slot)` pair; read-then-increment
  under the existing store lock on each claim update write
- `resolve_current_state()` sorts by `chain_seq` before applying supersede/retract
  decisions; `active_claims[-1]` after that sort is deterministic
- Legacy records without `chain_seq` degrade to current behavior; no error

**Constraints:** `chain_seq` is scoped to `(subject, slot)` — not global. Must not
require a full claim history scan on every write; a per-slot high-water-mark read
is sufficient.

---

## New architectural items

### #15 — Multi-store recall fan-out (Satorid)

**Status:** Not started — no spec exists  
**Blocks:** nothing  
**Blocked by:** nothing (can spec and prototype in parallel with other work)  
**Effort:** ~1 day spec + ~4 days implementation

**Problem:** The Satorid architecture requires a single `recall` call to fan out across
Core Memory (causal/transcript), Ragie (multi-modal documents and video), and PipeHouse
(relational data insights), combine results with normalized scores, and return a unified
evidence set with per-source provenance. No part of this exists. There is no spec for
the result shape, score normalization across heterogeneous stores, provenance tagging,
or degraded-mode behavior when one store is unavailable.

**Scope for the spec (produce this before writing any code):**

1. **Result envelope** — what does a unified `RecallResult` look like when evidence
   items come from three different stores? Each `EvidenceItem` must carry a
   `source_store` field (`"core_memory"` / `"ragie"` / `"pipehouse"`) and a
   `source_ref` (the store-native ID) in addition to the Core Memory `bead_id` (which
   may be absent for items that have no Core Memory bead).

2. **Score normalization** — Core Memory scores, Ragie relevance scores, and PipeHouse
   attribute-match scores are on different scales. Define the normalization contract
   before implementing. A per-store min-max rescale to [0.0, 1.0] before merging is
   the simplest defensible approach.

3. **Causal anchor invariant** — Core Memory remains the causal layer. Ragie and
   PipeHouse items surface as evidence; they do not own causal edges. The agent reasons
   across all three; it does not treat a Ragie chunk as a bead that can have `led_to`
   associations. Enforce this at the result-combination layer, not by convention.

4. **Degraded mode** — if Ragie or PipeHouse is unreachable, Core Memory recall
   proceeds and returns available results. The missing stores are reported in
   `RecallResult.metadata["unavailable_stores"]`, not silently omitted.

5. **Unifying ID convention** — a video ingested to both stores (transcript → Core
   Memory bead, chunks → Ragie) must share an ID so the agent can recognize at answer
   time that two result items refer to the same source event. Specify the field name,
   where it is written at ingest time, and how the combination layer surfaces it.

**Constraints:**
- Fan-out is at the `recall` endpoint layer, not inside Core Memory's internal
  semantic index. Do not plug Ragie into `semantic_index.py`.
- Each external store call is isolated — a Ragie timeout must not block Core Memory
  results from returning.

---

### #16 — External data bead ingest contract

**Status:** Not started — no spec exists  
**Blocks:** Satorid / PipeHouse integration (#15 depends on this)  
**Effort:** ~1 day spec + ~2 days implementation

**Problem:** The agreed integration model is: PipeHouse normalizes relational data,
writes insights to a DB table, Core Memory reads that table natively and generates a
bead, which can then participate in the associations/claims layer as context builds.
No part of this is specified. PipeHouse has no schema to build against. Core Memory
has no ingest path for externally-sourced data beads. The `led_to` / `supports` edges
in the architecture diagram have no guaranteed data to anchor to.

**Scope for the spec:**

1. **Bead schema for external data source** — what fields are required vs. optional
   on a bead created from a PipeHouse insight? Minimum required: `type` (e.g.
   `"data_insight"`), `source_system`, `source_table`, `source_record_id`,
   `as_of_timestamp` (the data timestamp, not the ingest timestamp), `entity_refs`
   (vendor, account, or other named entities present in the row), `attribute_tags`
   (PipeHouse-assigned labels), `content` (human-readable summary of the insight).

2. **DB table contract** — the schema of the table PipeHouse writes to and Core Memory
   reads from. Define column names, types, and which columns Core Memory queries on.
   This is the interface Chris needs to build against.

3. **Ingest path** — how does a row in that table become a bead? Options: (a) a
   scheduled job that polls the table and calls `emit_turn_finalized` for new rows;
   (b) a webhook from PipeHouse that triggers ingest on write. Specify which and where
   the code lives (`runtime/` subpackage, not `integrations/`).

4. **Unifying ID** — the `source_record_id` field is the join key that links a Core
   Memory bead back to the originating PipeHouse record (and transitively to a Ragie
   chunk when both reference the same event). Specify how this ID is stored on the bead
   — the `links` dict on `schema/models.py:Bead` is the current candidate. Name the
   key (`"external_source_id"`) and document the convention so all stores use the same
   field name.

5. **Association eligibility** — a PipeHouse bead participates in agent-judged
   association crawling exactly like any other bead. No special-casing. The `supports`
   edge from a data bead to a decision bead is assigned by the crawler under the
   standard contract.

**Constraints:**
- PipeHouse beads enter through `emit_turn_finalized`, not by writing to persistence
  directly. The public API write path invariant holds.
- The ingest job lives in `runtime/`, not in `integrations/`. PipeHouse is an
  external data source, not a framework adapter.

---

### #17 — Eval and benchmark layer

**Status:** Not started — baselines exist, no pipeline  
**Blocks:** nothing  
**Blocked by:** nothing  
**Effort:** ~3 days

**Problem:** LoCoMo baselines exist at `docs/benchmarks/locomo/baselines.md` but there
is no pipeline to run them, no way to track recall quality as features ship, and no
mechanism to validate that #11 (myelination), #13 (temporal recall), #14 (contradiction
pressure), and #15 (multi-store fan-out) actually improve recall rather than regress it.
Shipping improvements without measurement means relying on intuition for quality claims.
This matters especially for external credibility — "inspectable recall" requires a
benchmark delta to be credible.

**Scope:**

1. **LoCoMo runner** — a script that ingests LoCoMo conversation fixtures into a
   test Core Memory instance, runs the query set, compares answers to gold labels, and
   emits precision/recall/F1 per query type. Output is a JSON report that can be
   diffed across commits.

2. **Baseline capture** — run the runner on the current `main` branch and commit the
   result as `docs/benchmarks/locomo/baseline-YYYY-MM-DD.json`. Subsequent runs diff
   against the latest baseline.

3. **CI integration** — run the eval on PRs that touch `retrieval/`. Gate on
   no-regression (score may not drop more than 2pp vs. baseline). Full eval run is
   slow; a fast smoke subset (20 queries) runs on every PR; the full set runs nightly.

4. **Per-feature deltas** — each capability item (#11, #13, #14, #15) ships with a
   committed eval result showing its delta against baseline. This is the evidence for
   quality claims in external pitches.

**Constraints:**
- The eval runner must work against the `JsonFileBackend` (zero external deps) so it
  runs in CI without infrastructure.
- Do not build a custom benchmark format. Use LoCoMo fixtures as-is; the runner adapts
  to them, not vice versa.
- Eval scores are committed artifacts, not printed to stdout and discarded.

---

## Dependency graph

```
#0 (window merge)
├── #2 (goal lifecycle)
└── #6 (claim sequencing)

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
| 1 | **#0** window merge | 1d | Unblocks #2 + #6; surgical, low risk |
| 2 | **#13** temporal recall | 2d | No deps; high value, quick win |
| 3 | **#11** myelination | 2d | No deps; unblocks #14 and #12 |
| 4 | **#9B** enrichment delta | 3d | No deps; delivers #3/#7 as side effects |
| 5 | **#16** ingest contract spec | 1d spec | Unblocks #15; spec only, low cost |
| 6 | **#10** multi-speaker | 4d | Research first; foundational for multi-participant |
| 7 | **#14** contradiction pressure | 3d | After #11 |
| 8 | **#17** eval layer | 3d | Parallel to any of the above |
| 9 | **#15** multi-store fan-out | 5d | After #16 spec + Ragie subscription confirmed |
| 10 | **#2** goal lifecycle | 3d | After #0 |
| 11 | **#6** claim sequencing | 2d | After #0 |
| 12 | **#12** dreamer themes | 3d | After #11 + #14 |
