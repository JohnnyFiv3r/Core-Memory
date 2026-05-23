# SOUL.md Synthesis Specification

**Date:** 2026-05-17
**Status:** 2.0 implementation spec
**Depends on:** myelination (#11), claims layer (#6), Dreamer synthesis (#12), contradiction pressure (#14)

---

## Overview

SOUL.md is derived state — a synthesized, low-entropy projection of the causal graph's stable,
reinforced structure. It is not authored directly by the agent or the user. It is produced by the
`soul-synthesis` job from the myelination and claims layers, validated by the Dreamer's accepted
principles, and written to `.beads/identity/soul.md`.

The graph is the epistemic authority. SOUL.md is the identity surface the agent reasons *from*
each session — injected into working memory at session start, not retrieved on demand.

---

## Signal sources

### 1. Claims layer — stability signal

Source: `resolve_all_current_state(root)` across the full bead store.

Per claim slot `(subject, slot)`:

```
sessions_persisted    = count distinct session_ids in claim history for this slot
days_since_supersede  = days since last chain_seq advance on this slot
stability_score       = sessions_persisted / total_sessions_in_window
```

### 2. Myelination layer — retrieval utility signal

Source: `compute_myelination_bonus_map(root)` → `bonus_by_bead_id`.

Per bead carrying a claim:

```
myelination_bonus = bonus_by_bead_id[carrier_bead_id]
```

Reflects: how often has this bead been decisive (top-ranked evidence) in a successful
retrieval, time-decayed and success-weighted.

### 3. Dreamer principles — validated abstraction signal

Source: `list_dreamer_candidates(status="accepted")` filtered to `proposed_theme` type
with at least one confirmed downstream retrieval use (`recall_count > 0` on a constituent bead).

These are abstractions that survived both synthesis and retrieval validation. They enter the
candidate pool directly without needing the claims-layer stability check.

### 4. Salience policy — explicit priority signal

Source: beads tagged with user/domain salience labels (`safety-critical`, `legally-sensitive`,
`high-regret`). Policy-tagged candidates get a salience boost regardless of organic myelination.

---

## Eligibility gate — inner join

Neither myelination alone nor claim stability alone qualifies a candidate. Both must hold:

```
eligible(subject, slot) =
    stability_score(subject, slot)      >= MIN_STABILITY        # default: 0.6
    AND
    myelination_bonus(carrier_bead_id)  >= MIN_MYELINATION      # default: 0.06
    AND
    epistemic_conflict_score(subject, slot) < MAX_CONFLICT      # default: 0.4
```

**Why AND not OR:**
- High myelination, unstable claim: system frequently retrieves a changing belief — not identity-forming
- Stable claim, low myelination: belief is consistent but not load-bearing in actual reasoning — not identity-forming
- Both: belief is consistent AND shapes successful reasoning — eligible

---

## Scoring among eligible candidates

```
soul_score =
    0.25 * stability_score
  + 0.30 * myelination_bonus
  + 0.25 * dreamer_validated          # 1.0 if accepted principle exists and retrieved
  + 0.15 * salience_policy            # 1.0 if policy-tagged, 0.0 otherwise
  - 0.20 * epistemic_conflict_score   # contested beliefs score lower
  - 0.10 * recency_penalty            # days_since_first_seen < 14 → penalty
```

Myelination weighted highest because retrieval utility is the most direct signal that a belief
actively shapes reasoning, not merely exists in storage.

---

## Compression step — identity synthesis

Raw claim slots do not go into SOUL.md directly. `(architecture, db_preference) = PostgreSQL JSONB`
is evidence, not identity. The synthesis step produces the identity-level characterization:
what stable value or preference pattern is this claim an *instance of*?

This requires an LLM call. Rule-based compression cannot produce identity-level abstraction.

### Clustering

Before the LLM call, cluster eligible candidates by semantic proximity:
- Shared entity context (same entities appear in multiple claim subjects)
- Related subject prefixes (`architecture/`, `engineering/`)
- Linked via associations in the bead graph

One LLM call per cluster. Clusters of one (isolated claim with no related candidates) are
evaluated independently.

### Synthesis prompt inputs

Per cluster:
- Eligible candidates: claim slots with current values, confidence, myelination scores,
  evidence bead IDs, related Dreamer principles
- Current SOUL.md content for diff awareness
- Instruction: produce identity-level characterizations, not event summaries. Express stable
  values, preferences, and commitments — not "chose PostgreSQL" but "what kind of mind
  consistently makes this choice and why."

### Synthesis output constraints

Every entry the LLM produces must cite at least one `claim_id` or `principle_bead_id` from
the input evidence set. Synthesis without grounded evidence is quarantined (same rule as
Dreamer candidates with no `related_bead_ids`). This is enforced structurally: the synthesis
call uses structured output (JSON schema), and the evidence field is required.

---

## SOUL.md schema

Stored at `.beads/identity/soul.md`. Structured markdown, not prose.

```markdown
# Identity Surface
*Generated: {timestamp} | Synthesis version: {n} | Job: soul-synthesis-v1*

## Stable Preferences

**{Identity-level characterization}**
Confidence: {float} | Myelination: {float} | Sessions persisted: {n}
Evidence: `{claim_id_1}`, `{claim_id_2}`, `{principle_bead_id}`
> {Plain-text elaboration — 1-3 sentences}

## Persistent Commitments

**{...}**
{same schema}

## Recurring Goals

**{...}**
{same schema}

## Uncertainty Markers

**{topic}**
Confidence: {float} | Status: contested
Evidence: `{claim_id}` — {n} conflicting claims, unresolved
> {Description of the tension, not a resolution}
```

**Schema invariants:**
- Every entry carries explicit `confidence`, `myelination`, and `sessions_persisted`
- Every entry carries at least one evidence reference (`claim_id` or `principle_bead_id`)
- Uncertainty markers are first-class — contested beliefs get their own section, not excluded
- No entry is added in a single synthesis run at confidence above `SOUL_MAX_INITIAL_CONFIDENCE`
  (default: 0.75). Confidence can only rise through successive synthesis cycles.

---

## Job mechanics

```
soul-synthesis job:

1.  Load resolve_all_current_state(root)
2.  Load compute_myelination_bonus_map(root)  [from myelination manifest cache]
3.  Load list_dreamer_candidates(status="accepted")
4.  Inner join: claims × myelination by carrier bead ID
5.  Filter by eligibility gate
6.  Add Dreamer principles passing downstream-retrieval check
7.  Compute soul_score per candidate
8.  Cluster candidates by semantic proximity
9.  LLM synthesis call per cluster → identity-level entries with evidence refs
10. Validate: quarantine any entry missing evidence refs
11. Generate diff against current soul.md
    [additions, updates, deprecations]

    In 2.0:
      Write diff to .beads/events/soul-synthesis-pending.jsonl
      Surface to user for review
      On acceptance: apply diff

    In Satorid:
      Auto-apply entries where soul_score > SOUL_AUTO_THRESHOLD (default: 0.85)
        AND confidence > 0.85
        AND no active contradiction pressure
      Queue remainder for review

12. Write .beads/identity/soul.md
13. Append synthesis record to .beads/events/soul-synthesis.jsonl
14. Archive previous soul.md version (append-only history)
```

---

## Trigger — sleep pressure

`soul-synthesis` runs at the **slowest cadence** of all maintenance jobs. It triggers when
`sleep_pressure` crosses `SOUL_SYNTHESIS_THRESHOLD`, which is set higher than the Dreamer
consolidation threshold:

```
sleep_pressure =
    new_bead_count_since_last_synthesis / NEW_BEAD_WEIGHT
  + myelination_landscape_shift         # Σ |Δbonus| since last synthesis
  + accepted_dreamer_principles_count   # new validated abstractions available
  + sessions_elapsed_since_last         # time component — never synthesizes more
                                        # than once per MIN_SYNTHESIS_INTERVAL
```

`MIN_SYNTHESIS_INTERVAL` defaults to 14 days. SOUL.md should evolve like consolidated
personality, not like a chat summary.

---

## Feedback loop and stabilizers

**The reinforcement loop:**
SOUL.md entry injected into working memory → agent retrieval patterns shift toward that
preference → myelination reinforces matching paths → soul-synthesis finds stronger evidence
→ SOUL.md confidence increases on next cycle.

**Stabilizer 1 — confidence floor:**
New entries are capped at `SOUL_MAX_INITIAL_CONFIDENCE = 0.75`. Confidence can only increase
across successive independent synthesis cycles. A single strong session cannot produce a
high-confidence SOUL.md entry.

**Stabilizer 2 — contradiction pressure:**
`epistemic_conflict_score > MAX_CONFLICT` gates eligibility. A belief that is actively
contested in the claim graph cannot accumulate toward SOUL.md threshold. The conflict must
be resolved (via supersede, user resolution, or natural dominance) before the slot becomes
eligible again.

**Stabilizer 3 — evidence requirement:**
Every SOUL.md entry must maintain live evidence references. If the underlying claim is
retracted or superseded, the entry's confidence is reduced and flagged for re-synthesis on
the next cycle. An entry without surviving evidence is deprecated automatically.

---

## Authority invariant

SOUL.md has behavioral authority (injected into working memory every session) but not
epistemic authority (cannot override the graph). The invariant:

- SOUL.md can only be written by `soul-synthesis`, never by the agent acting on turn input
- Contradiction pressure detection (#14) scans SOUL.md entries in addition to claim pairs
- A graph claim contradicting a SOUL.md entry triggers re-synthesis of that entry and
  surfaces the inconsistency — it does not silently override
- SOUL.md entries are not beads and are not visible to the crawler — they are a derived
  projection surface, not graph content

---

## Files

| File | Purpose |
|------|---------|
| `.beads/identity/soul.md` | Current identity surface |
| `.beads/events/soul-synthesis.jsonl` | Synthesis history (append-only) |
| `.beads/events/soul-synthesis-pending.jsonl` | Pending diffs awaiting review (2.0) |
| `core_memory/runtime/soul_synthesis.py` | New module |
| `core_memory/runtime/jobs.py` | Add `soul-synthesis` job kind |
| `core_memory/runtime/turn_flow.py` | Inject `soul.md` into session working memory |
| `core_memory/retrieval/agent.py` | Include soul.md content in recall context |
