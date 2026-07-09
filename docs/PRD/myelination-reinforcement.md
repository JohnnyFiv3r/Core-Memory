# PRD 3: Myelination V2
## Audited Association Reinforcement, Decay, and Goal-Directed Navigation

**Status:** Implemented (v2 spec shipped)
**Supersedes:** `docs/PRD/myelination-v2-continuity-strength.md` — its Slice A
(unified edge strength) folds into this PRD; Slice B (per-bead continuity-depth
scalar) migrates to Dreamer V3 **Assembly Depth**; Slice C (geometry/projection
export) migrates to Dreamer V3 §16.1 (deferred). See §0.

> **Review fixes folded into this draft (vs. the external draft):**
> - Added §0 Supersession + scope split (what stays here vs. moves to Dreamer).
> - Added §16.2 Reward-event ↔ manifest fusion formula (was unspecified).
> - Added §9.3 "Concrete supporting edges" derivation (the lynchpin, was one line).
> - Claim-conflict decay specified as an edge-level reward event; the bead-level
>   `apply_contradiction_decay` marked legacy/compatibility-only (Codex P2).
> - Added §12.3 target states are not a myelination reward source (Dreamer V4).
> - Brand-neutral throughout (public naming guard).

---

## 0. Supersession & scope

This PRD is the canonical **edge-reinforcement** spec. It supersedes
`myelination-v2-continuity-strength.md`, whose three slices are redistributed:

| Old myelination-v2 slice | Disposition |
|---|---|
| Slice A — unified edge strength (`quality × usage × recency × status`) | Folds into this PRD's scoring foundation (§18) |
| Slice B — per-bead `continuity_depth` scalar | Migrates to **Dreamer V3 Assembly Depth** |
| Slice C — geometry/continuity projection export | Migrates to **Dreamer V3 §16.1** (deferred, Phase 3) |

Myelination V2 here is narrowed deliberately: it is about **reinforcing and
decaying audited association pathways**, nothing more. Measurement of structural
depth and any visualization projection are Dreamer's, since Dreamer owns the
depth/continuity measure.

---

## 1. Purpose

Myelination is Core Memory's reinforcement layer. It strengthens historically
useful association pathways and weakens historically unhelpful ones using audited
system events. Myelination is not memory, interpretation, identity, or truth. It
is reinforcement over navigation paths, answering: **which association paths have
demonstrably helped the system navigate successfully?**

---

## 2. Core Principle

Biological myelination strengthens connections, not neurons. Core Memory follows
the same principle: myelination operates on **associations / traversed edges**,
not beads, claims, storyline overlays, Dreamer findings, or SOUL statements.
Beads remain immutable evidence; claims remain governed by C/B/A, grounding,
supersession, and claim updates; overlays remain interpretation; SOUL remains
self-model theory; Dreamer remains scientist.

---

## 3. Relationship To Current Core Memory

Core Memory already includes an experimental implementation that is opt-in via
`CORE_MEMORY_MYELINATION_ENABLED`, reads `.beads/events/retrieval-feedback.jsonl`,
extracts traversed chain edges from recall responses, counts retrieval
success/failure, computes `bonus_by_edge_key`, projects edge bonus onto endpoint
beads (`bonus_by_bead_id`) for scorer compatibility, does not mutate beads/claims,
uses no time decay, and applies telemetry-driven positive/negative bonus
(`compute_myelination_bonus_map` in `runtime/observability/myelination.py`). A
bead-level `apply_contradiction_decay` helper also exists there, applied at
manifest-projection time; V2 treats it as **legacy/compatibility only** — new
decay sources are edge-level reward events (§16), not bead-level penalties. V2
extends this implementation rather than replacing it.

---

## 4. Architectural Role

Stack: beads → claims → associations → storyline backbones → storyline overlays →
SOUL → Dreamer. Myelination lives at **associations** and influences higher
layers only indirectly: storyline backbones may become more salient because their
substrate associations are reinforced. Overlays are never reinforced directly.

---

## 5. What Receives Myelination

### 5.1 Primary target
Associations / traversed edges (`goal→outcome`, `decision→outcome`,
`claim→supporting evidence`, `cause→effect`, `bead A→bead B via association`). The
association receives reinforcement or decay; the beads do not.

### 5.2 Edge key
`source|relationship|target` (the current convention). V2 preserves it unless
explicitly migrated.

---

## 6. What Does Not Receive Myelination

- **Beads** — immutable evidence. They may receive a *projected* scoring bonus
  (`bonus_by_bead_id`) for runtime compatibility, derived from edge myelination;
  this is not bead decay.
- **Claims** — governed by C/B/A, grounding, authority, claim updates,
  supersession, conflict resolution. Myelination must not mutate claim truth.
- **Storyline overlays** — interpretation must not become self-validating.
- **Dreamer findings** — hypotheses; they do not self-reinforce.
- **SOUL statements** — falsifiable self-model theory; they do not self-reinforce.

---

## 7. Audited Reward Sources

Myelination must be grounded in audited events, never vague impressions ("this
felt useful," "Dreamer liked this"). Valid reward/decay sources come from
recorded system events (§8–§13).

## 8. Source 1: Retrieval Feedback
`.beads/events/retrieval-feedback.jsonl` records query, intent, result bead ids,
claim slots, traversed chain edges, answer outcome, success/failure. Edges
recurring in successful retrievals gain myelination; edges recurring in failed
retrievals lose it. This is the current safest, most mature source.

## 9. Source 2: Human Confirmation / Approval

Hooks into existing surfaces: `confirm_bead`, `approve_bead`, `reject_bead`,
`request_approval`, `list_pending_approvals`. Human confirmation sets
`authority=user_confirmed`, `confidence_class=A`, and appends a confirmation
event; myelination consumes these events.

### 9.1 Positive
When a human confirms/approves a bead, accepts a Dreamer candidate, approves a
goal candidate, approves a SOUL update proposal, or marks a recommendation
useful — reward is assigned **only to concrete supporting edges** (§9.3). No edge
path ⇒ no myelination event.

### 9.2 Negative
When a human rejects a bead/candidate/goal/SOUL proposal or marks a
recommendation wrong — penalty applies **only to concrete supporting edges**. No
edge path ⇒ record the rejection for governance, do not myelinate.

### 9.3 "Concrete supporting edges" derivation (the lynchpin)

For a target bead `b` (confirmed/approved/rejected), the supporting edge set is:

1. **Evidence edges** — associations incident to `b` with evidential relationships
   (`supports`, `derived_from`, `caused_by`, `resolves`, incoming `led_to`).
2. **Recall-trace edges** — the traversed chain edges from the retrieval that
   surfaced `b`, recovered from `retrieval-feedback.jsonl` for the originating
   query (same extraction as Source 1).

The reward/decay applies to the union of (1) and (2), each clamped per §18. If
both sets are empty, **no myelination event is emitted** — a confirmation with no
traceable path reinforces nothing. This rule is what keeps human reward from
smearing across the whole graph.

## 10. Source 3: Goal Resolution

Grounded in the existing goal lifecycle (`promotion_service.py`): a goal resolves
when an outcome bead exists, a visible candidate goal exists, the outcome matches
by shared tags/token overlap, a `resolves` association is emitted, and the goal
transitions to `goal_status=resolved` (`promotion_state=resolved`,
`promotion_decision="resolve_goal"`, `resolved_by_bead_id=<outcome>`).

### 10.1 Valid goal reward event
Requires the goal bead, outcome bead, `resolves` association, `goal_status=resolved`,
`resolved_by_bead_id`, and `promotion_decision=resolve_goal`. The `resolves`
association is the primary edge to reinforce; explicitly-present supporting edges
in the resolution evidence path may receive bounded reward.

### 10.2 Invalid sources
Vague goal progress, inferred usefulness, Dreamer's belief that a goal advanced,
SOUL saying a goal is important, future-projection alignment, agent intuition.
Goal-progress reward is a future feature only if backed by explicit audited
progress events.

## 11. Source 4: Dreamer Candidate Decisions
Accept/reject **decisions** on Dreamer candidates (narrative, retrieval-value,
entity-merge, contradiction-resolution, etc.) may reinforce/weaken their concrete
supporting edges.

### 11.1 Guardrail
Dreamer findings themselves are not rewarded — only human/governance decisions
about them. Dreamer is an evidence source, not a reward source.

## 12. Source 5: Claim Conflict Resolution
Tension resolution is too abstract for direct myelination; claim conflict
resolution is concrete. When a conflict is resolved via an audited claim update or
contradiction-review decision, the **concrete supporting edges** (§9.3) of the
involved claims may be reinforced/decayed: preferred-claim path reinforced;
contradicted/retracted path weakened; both-valid context fork creates scoped
resolution, not blanket punishment.

Decay here is **edge-level**, emitted as a negative `myelination_reward_event.v1`
(§16) on the specific supporting `edge_key`s — never a bead-level penalty. Do
**not** model this on the existing `apply_contradiction_decay` helper: that helper
is bead-level (it subtracts from `bonus_by_bead_id` for a conflicting source bead)
and would penalize every path sharing that endpoint bead, violating the edge-only
invariant (§2, §6). `apply_contradiction_decay` is retained only as a
legacy/compatibility projection-time adjustment and is not the model for this
source.

### 12.3 Target states are not a reward source

Dreamer V4 (target states / inferred attractors, see Dreamer PRD §31) is an
*inference* layer. Myelination must not reward "movement toward a target state" —
that would reinforce an unendorsed inference. Myelination reinforces only audited
paths toward **approved or resolved** outcomes. A target state becomes eligible to
influence reward only after SOUL endorses it into a goal and that goal resolves
through the audited goal lifecycle (§10).

## 13. Source 6: Overlay Decide Flow
Overlays are interpretive and receive no direct myelination. Accepted/superseded
overlays may provide indirect signal to their substrate: accepted narrative
candidate → reinforce supporting association edges if present;
superseded/rejected → weaken them if present. Never reinforce the overlay object,
increase overlay confidence via myelination, or treat an accepted overlay as
backbone evidence.

---

## 14. Tension Handling

Tensions are computed in the storyline projection (competing overlays, claim-slot
conflicts), not stored canonical objects, so V2 must not reward "tension
resolution" abstractly.

### 14.1 Valid proxies
Claim conflict resolved; competing overlay rejected/superseded; goal conflict
resolved through explicit goal decision; human approval resolves a contradiction
candidate.

### 14.2 Invalid sources
"Dreamer says a tension was resolved"; "SOUL says a tension is reduced"; "agent
feels the user is less conflicted"; "future projection appears cleaner";
"narrative coherence improved." These may become Dreamer findings or SOUL
proposals, never myelination reward.

---

## 15. Decay

Decay is the inverse of reinforcement: it weakens traversal preference, does not
delete, and applies to association paths, not evidence.

- **15.1 Current:** negative edge bonus from failed retrieval feedback (primary
  V2 decay source).
- **15.2 Additional:** human rejection; rejected Dreamer candidate; rejected goal
  candidate; unresolved retrieval failure; claim conflict resolved against a path;
  overlay supersession when its supporting path is no longer accepted. Each must
  link to concrete supporting edges.
- **15.3 No time decay in V2.** Older pathways are not inherently worse; decay
  stays event/telemetry-driven.

---

## 16. Reward Event Model

### 16.1 Event
First-class audited event `myelination_reward_event.v1`, stored at
`.beads/events/myelination-rewards.jsonl`, recording reward/decay without mutating
evidence:

```json
{
  "schema": "myelination_reward_event.v1",
  "id": "mr-...",
  "created_at": "...",
  "source_type": "retrieval_feedback|human_approval|human_rejection|goal_resolution|dreamer_candidate_decision|claim_conflict_resolution|overlay_decision",
  "source_event_id": "...",
  "polarity": "positive|negative",
  "strength": 0.0,
  "edge_keys": ["source|relationship|target"],
  "supporting_bead_ids": [],
  "supporting_claim_ids": [],
  "supporting_candidate_ids": [],
  "reason": "...",
  "guardrails": {"requires_concrete_edges": true, "mutates_beads": false, "mutates_claims": false, "mutates_overlays": false, "mutates_soul": false}
}
```

### 16.2 Reward-event ↔ manifest fusion (was unspecified)

The manifest's `bonus_by_edge_key` is computed from **two summands per edge**:

```
feedback_bonus[ek] = (existing telemetry signal from retrieval-feedback.jsonl)
reward_bonus[ek]   = Σ over myelination-rewards.jsonl events touching ek:
                       polarity_sign · strength
edge_bonus[ek]     = clamp( feedback_bonus[ek] + reward_bonus[ek],
                            -NEG_CAP, +POS_CAP )
```

- Reward events are a second additive input to `compute_myelination_bonus_map`;
  the existing retrieval-feedback path is unchanged and remains the default when
  reward events are absent.
- Min-hit thresholding (`MIN_HITS`) applies to the combined hit count.
- `bonus_by_bead_id` remains a projection of `edge_bonus` onto endpoint beads
  (unchanged), and must not be read as bead decay.

---

## 17. Myelination Manifest

Preserve the manifest pattern; output `.beads/events/myelination-manifest.json`:

```json
{
  "schema": "core_memory.myelination_manifest.v2",
  "created_at": "...",
  "enabled": true,
  "bonus_by_edge_key": {},
  "bonus_by_bead_id": {},
  "stats": {},
  "config": {},
  "source_event_counts": {}
}
```

---

## 18. Scoring Model

Combines retrieval-feedback success/failure, explicit reward events, negative
reward events, minimum-hit thresholds, and positive/negative bonus caps. Existing
env vars remain: `CORE_MEMORY_MYELINATION_ENABLED`, `_SINCE`, `_LIMIT`,
`_MIN_HITS`, `_POS_CAP`, `_NEG_CAP`. New optional:
`CORE_MEMORY_MYELINATION_REWARD_EVENTS_ENABLED`,
`CORE_MEMORY_MYELINATION_REWARD_EVENT_LIMIT`.

---

## 19. Human Approval Hook Points

`confirm_bead` / `approve_bead` → reinforce supporting edges for the
confirmed/approved bead. `reject_bead` → weaken supporting edges.
`decide_dreamer_candidate` → accept reinforces / reject weakens supporting edges.
`resolve_goal_candidate_for_store` → reinforce `outcome --resolves--> goal` and
present visible supporting edges. Claim conflict review
(`contradiction_pressure_candidate` decision, claim update, context fork) →
reinforce resolved paths, weaken retracted paths, do not punish both-valid forks
globally. All subject to §9.3 (concrete supporting edges).

---

## 20. Goal Resolution Grounding

Goals are judged resolved only by the goal-lifecycle system; myelination
**consumes** that, it does not judge it.
- **20.1 Valid:** `goal_status=resolved`, `promotion_state=resolved`,
  `promotion_decision=resolve_goal`, `resolved_by_bead_id=<outcome>`, `resolves`
  association present/emitted.
- **20.2 Response:** reinforce the `resolves` association, reinforce explicitly
  present supporting edges, write `myelination_reward_event.v1`, refresh the
  manifest.
- **20.3 Non-role:** must not decide resolution, promote, abandon, decay a goal
  directly, or create goal-lifecycle state.

---

## 21. Goal Decay

Remains a SOUL / Dreamer / goal-lifecycle concern, not core myelination in V2.
Myelination may provide evidence that a goal path is unused/unrewarded but does
not mark goals dormant or abandoned. Goal decay requires a separate
goal-lifecycle or SOUL governance decision.

---

## 22. Tension Resolution Grounding

Tensions are not first-class persisted objects, so V2 cannot resolve them
directly. Myelination may consume concrete tension-proxy events (§14.1). Dreamer
may later infer "this tension appears reduced," but that inference must not
directly myelinate edges.

---

## 23. Storyline Interaction

Storylines receive no direct myelination; backbones inherit signal from their
association substrate (reinforced associations → stronger traversed path → more
salient backbone). Overlay confidence must not be changed by myelination.

---

## 24. Dreamer Interaction

Dreamer may **consume** myelination outputs (edge bonus, bead projection bonus,
reward-event history, manifest stats, decay history) as evidence for Assembly
Depth, narrative strength, attractor strength, goal/storyline decay warnings.
Dreamer may not modify myelination; it may only propose candidates whose later
human/governance decision produces reward.

---

## 25. SOUL Interaction

SOUL may define/endorse goals but does not modify myelination. A SOUL update
triggers reward only if approved through an audited approval flow **and** it
references concrete supporting edges. Otherwise SOUL updates remain self-model
maintenance, not reinforcement.

---

## 26. Tests / Acceptance Criteria

Retrieval feedback still produces edge bonuses; failed retrieval produces
negative bonus; reward events without edge keys do not affect myelination;
confirm/approve/reject can emit reward events; goal resolution reinforces only the
`resolves` edge and explicit support edges; myelination does not decide goal
resolution; tension resolution is not rewarded without a concrete proxy event;
overlays receive no myelination; accepted overlays only reinforce supporting
substrate edges; C/B/A is never mutated; bead content is never mutated; claims are
never mutated; `bonus_by_bead_id` is projection only; time decay is not applied;
reward-event + feedback fusion (§16.2) is deterministic and cap-clamped;
claim-conflict decay targets specific `edge_key`s and does **not** penalize
unrelated paths sharing an endpoint bead (edge-only invariant).

---

## 27. Non-Goals

Does not determine truth; create/resolve/abandon goals; resolve tensions; create
storylines/overlays; update SOUL or Dreamer findings; mutate beads/claims/C-B-A;
reinforce interpretations directly; reward movement toward target states (§12.3);
or implement time decay.

---

## 28. Summary

Core Memory stores evidence. Storylines organize continuity. Overlays interpret
it. Dreamer studies it. SOUL maintains selfhood and goals. Myelination reinforces
audited association pathways — grounded in concrete events (retrieval feedback,
human approval/rejection, goal resolution, candidate decisions, claim-conflict
resolution, overlay decide flow), never in abstractions like "goal progress,"
"tension resolution," or "target-state movement" unless represented by concrete
audited events. The central invariant: **myelination changes traversal
preference, never truth.**
