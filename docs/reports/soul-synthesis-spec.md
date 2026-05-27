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

## Problem statement and user value

Most AI memory systems become ever-growing sludge. They accumulate facts without forming
identity. An agent that has processed 10,000 turns has 10,000 facts but no stable self —
each session starts from scratch, preferences are re-retrieved rather than remembered, and
the agent cannot tell the difference between what it *knows* and what it *is*.

SOUL.md solves this by maintaining a stable, compressed, human-readable identity surface
that is injected at session start. The agent does not retrieve its identity — it has one.
The difference is felt immediately: an agent with SOUL.md gives consistent answers to
preference questions, maintains stable values across sessions, and reasons from its history
rather than recalling it.

Without SOUL.md: "What database would you recommend?" requires retrieving past decisions.
With SOUL.md: the agent already knows it has a strong preference for operational simplicity
and gives a consistent, grounded answer from the first token.

---

## Scope

**In:**
- `soul-synthesis` job reading myelination + claims + Dreamer signals
- `soul_loader.py` discovery, classification, adapter registration
- `.beads/identity/soul.md` as the output artifact
- Working memory injection at session start via `turn_flow.py`
- Synthesis event log and pending-diff file for review (2.0 mode)
- CLI inspection commands (`soul status`, `soul show`, `soul history`, `soul accept/reject`)
- Adapter stubs for OpenClaw and Hermes

**Out:**
- SOUL.md does not affect the bead graph — it is derived, not authoritative
- `soul-synthesis` does not produce session recaps or summaries of recent events
- SOUL.md is not synchronized across agent instances by default
- No dedicated review UI — the pending diff file is the interface; rendering is a separate surface
- SOUL.md does not replace manually maintained `AGENT_INSTRUCTIONS.md` or `CLAUDE.md`;
  it coexists as the dynamically synthesized complement

---

## Hard dependencies

| Dependency | Required for | Status |
|------------|--------------|--------|
| Claims `chain_seq` + `resolve_all_current_state()` | stability signal | Done |
| Myelination wiring (#11) | myelination signal | Not yet wired to planner |
| Contradiction pressure (#14) | eligibility gate conflict check | Not yet implemented |
| Dreamer `proposed_theme` acceptance (#12) | third signal source | Optional — degrades gracefully |

SOUL.md can ship with only the claims layer complete. The AND gate means it produces no
entries when myelination or Dreamer signals are absent — cold start behavior, not a bug.
Add signals progressively as their owning features land.

---

## LLM synthesis call

- **Model:** small/cheap — this runs offline, not on the hot path. Haiku or equivalent.
  Never Opus; synthesis quality does not require frontier capability.
- **Output format:** structured JSON constrained by schema — not free text. Required fields:
  `text`, `evidence_refs` (≥1 item), `confidence`, `section`
  (`stable_preferences | persistent_commitments | recurring_goals | uncertainty_markers`)
- **Evidence validation:** before applying any diff entry, verify each cited `claim_id` and
  `principle_bead_id` exists in the store. Entries citing nonexistent evidence are quarantined
  with reason `"evidence_not_found"` — not applied, not discarded, reviewable.
- **Failure handling:** if the LLM call fails or returns invalid JSON, skip this synthesis
  cycle entirely. Do not partially apply. Do not modify the existing SOUL.md. Log the failure
  to `soul-synthesis.jsonl` with `status: "failed"`. Increment sleep pressure so the next
  cycle triggers sooner.
- **Prompt version:** the synthesis prompt carries a semantic version (`soul-synthesis-prompt-v1`).
  Stored in the synthesis event log so every SOUL.md entry is traceable to the prompt that
  produced it.
- **Token budget:** cap input at 4,000 tokens per cluster. If a cluster exceeds this, split
  by semantic proximity and run multiple calls. Never truncate evidence silently.

---

## Working memory injection

`soul_loader.discover(root, harness_adapter)` runs at session start in `turn_flow.py`,
before the first turn is processed.

**Injection structure:**

```
[IDENTITY SURFACE]
{soul.md identity_projection content — filtered by token budget}

[BEHAVIORAL CONTEXT]
{harness_policy content — AGENT_INSTRUCTIONS.md, CLAUDE.md, etc.}
```

Identity surface appears first — it shapes how the agent interprets the behavioral context,
not the other way around.

**Token budget enforcement:**
- `SOUL_INJECTION_MAX_TOKENS` (default: 800)
- When SOUL.md exceeds budget: include entries in descending `soul_score` order until budget
  is reached. Always include all `uncertainty_markers` (they are safety-critical context).
- When empty or cold start: omit the `[IDENTITY SURFACE]` block entirely — no empty section.

**What the agent sees:**
The agent is told explicitly: *"The Identity Surface below reflects your stable preferences,
commitments, and beliefs as synthesized from your causal history. Reason from this context;
do not treat it as retrieved facts to be cited."* The agent knows it is reading its own
identity, not external evidence. This framing prevents the agent from citing SOUL.md entries
as sources for factual claims.

---

## Success criteria

**Structural (mechanically testable):**
1. Every applied SOUL.md entry has ≥1 live evidence reference (claim_id or principle_bead_id
   that exists in the store)
2. No entry was applied at confidence above `SOUL_MAX_INITIAL_CONFIDENCE` on first synthesis
3. Deprecated entries are archived, not deleted; the previous SOUL.md version is preserved
4. Running `soul-synthesis` twice on identical state produces SOUL.md with same entries,
   same evidence refs, confidence within ±0.05 (idempotency)
5. A fresh deployment with zero sessions produces an empty SOUL.md, not a hallucinated one

**Stability (testable over time):**
6. A high-confidence entry present at synthesis N is still present at synthesis N+3 if no
   underlying claim was superseded or contradicted
7. An entry whose evidence claims were all retracted is deprecated within one synthesis cycle

**Compression quality (rubric — human review):**
8. SOUL.md entries cannot be reverse-engineered to reconstruct specific events (if yes: summary,
   not identity)
9. Two independent agents with similar claim histories produce semantically equivalent
   SOUL.md entries (convergent synthesis — the identity reflects the graph, not the synthesis
   prompt's phrasing variation)

**Integration:**
10. `soul_loader.discover()` on a root with no soul-related files returns `cold_start: True`
    and does not raise
11. An existing user-authored `SOUL.md` survives soul-synthesis without its pinned entries
    being overwritten

---

## Observability — CLI surface

All commands output JSON. All are read-only except `accept`/`reject`/`reset`.

- `core-memory soul status` — entry count, last synthesis timestamp, confidence distribution,
  whether a pending diff exists, sleep pressure current value vs. threshold
- `core-memory soul show` — current SOUL.md rendered to stdout
- `core-memory soul history [-n N]` — last N synthesis events from event log (default 5)
- `core-memory soul diff` — pending diff entries awaiting review (2.0 mode)
- `core-memory soul accept [--entry-id ID | --all]` — accept pending diff entries
- `core-memory soul reject [--entry-id ID] [--reason TEXT]` — reject with reason
- `core-memory soul reset` — archive current SOUL.md and zero sleep pressure; synthesis
  restarts from scratch on next trigger. Does not delete evidence from the graph.

---

## Soul discovery and harness adapter integration

Core Memory should not assume it is the first or only system to have produced an identity
document. A deployment may already have a manually authored `SOUL.md`, an
`AGENT_INSTRUCTIONS.md` from OpenClaw, a `CLAUDE.md` from Claude Code, or a harness-specific
identity file from Hermes or a custom integration. The soul loader detects and classifies
whatever exists before injecting into working memory or running synthesis.

### Source type classification

Not all SOUL.md-style documents are treated the same way. Two distinct roles:

| Role | Purpose | Synthesis treatment | Writeable by soul-synthesis |
|------|---------|--------------------|-----------------------------|
| `identity_projection` | Who the agent is — values, preferences, stable beliefs | Feeds synthesis pipeline as baseline; soul-synthesis extends and updates | Yes, with pinned-entry protection |
| `harness_policy` | How the agent behaves — instructions, constraints, adapter rules | Injected into working memory as-is; never touched by synthesis | No |

`AGENT_INSTRUCTIONS.md` and `CLAUDE.md` are `harness_policy`. They tell the agent how to
behave. SOUL.md tells the agent who it is. Conflating them would let soul-synthesis
accidentally overwrite policy documents with identity projections.

### Detection priority order

```
soul_loader.discover(root, harness_adapter=None):

1. CORE_MEMORY_SOUL_PATH env var
      → explicit path override; use as-is regardless of format

2. .beads/identity/soul.md
      → synthesized soul (native format)
      → role: identity_projection

3. {root}/SOUL.md
      → user-authored soul
      → role: identity_projection, all entries pinned: true

4. Harness adapter registered sources (in adapter priority order)
      → role and format declared by adapter at registration
      → examples: AGENT_INSTRUCTIONS.md, CLAUDE.md, .hermes/context.md

5. None found → cold start, empty identity surface (correct, not an error)
```

### Format detection

A discovered file is classified by its content:

- **`core_memory_soul_v1`** — has `# Identity Surface` header AND entries with `Confidence:`,
  `Myelination:`, `Evidence:` fields. Full parse; entries carry existing metadata.
- **`prose_markdown`** — any markdown file without the synthesized schema markers.
  Inject as-is; treat all entries as `pinned: true`.
- **`json`** / **`yaml`** — structured formats from non-markdown harnesses. Adapter provides
  a field mapping to the soul entry schema.

When format cannot be determined, default to `prose_markdown` — safest, never corrupts.

### Adapter registration

Harness adapters declare their soul sources at initialization time rather than requiring
Core Memory to know every possible harness location:

```python
# In the OpenClaw adapter:
register_soul_source(
    path="AGENT_INSTRUCTIONS.md",
    format="prose_markdown",
    role="harness_policy",
    priority=4
)

# In a Hermes integration:
register_soul_source(
    path=".hermes/identity.md",
    format="prose_markdown",
    role="identity_projection",
    priority=3
)

# Custom enterprise harness with JSON identity file:
register_soul_source(
    path="config/agent-identity.json",
    format="json",
    role="identity_projection",
    priority=3,
    field_map={"persona": "text", "core_values": "elaboration"}
)
```

### SoulDiscoveryResult

```python
@dataclass
class SoulDiscoveryResult:
    found: bool
    sources: list[SoulSource]       # all discovered files, in priority order
    injection_content: str          # merged content for working memory injection
    synthesis_baseline: str | None  # identity_projection content only, for soul-synthesis
    cold_start: bool                # True if no sources found
```

`injection_content` merges all sources with `harness_policy` sources first (they set the
behavioral frame), followed by `identity_projection` sources (they provide the identity
surface). The agent reads both but soul-synthesis only sees `synthesis_baseline`.

### Pinned entry protection

Entries in user-authored (`prose_markdown`) sources are marked `pinned: true`. Soul-synthesis:

- Never deprecates a pinned entry unless `epistemic_conflict_score > 0.7` against it
- May add synthesized entries alongside pinned entries
- After two successive synthesis cycles in which a pinned entry survives without contradiction,
  it is absorbed as a soul-synthesis-owned entry (gains `confidence`, `myelination`, `evidence`
  fields from the closest matching synthesized claim)

This means a user who starts with a hand-authored `SOUL.md` sees it preserved and gradually
enriched with evidence metadata, rather than overwritten.

### Synthesis interaction with discovered sources

If soul-synthesis finds an existing `identity_projection` source (synthesized or prose):

1. Parse existing entries as the diff baseline
2. Entries already present at high confidence with live evidence → skip (no redundant synthesis)
3. Entries present but missing evidence refs (prose) → attempt to match against current claim
   state; if matched, add evidence refs; if unmatched, leave as prose with `pinned: true`
4. New candidates from myelination/claims not present in existing source → synthesize and add

The result: soul-synthesis is additive and non-destructive on first encounter with any
existing document it didn't produce.

---

## Files

| File | Purpose |
|------|---------|
| `.beads/identity/soul.md` | Current identity surface (synthesized) |
| `.beads/events/soul-synthesis.jsonl` | Synthesis history (append-only) |
| `.beads/events/soul-synthesis-pending.jsonl` | Pending diffs awaiting review (2.0) |
| `core_memory/runtime/soul_synthesis.py` | Synthesis job |
| `core_memory/runtime/soul_loader.py` | Discovery, classification, adapter registration |
| `core_memory/runtime/jobs.py` | Add `soul-synthesis` job kind |
| `core_memory/runtime/turn_flow.py` | Call `soul_loader.discover()`, inject result into session context |
| `core_memory/retrieval/agent.py` | Include `injection_content` in recall context |
| `core_memory/adapters/openclaw.py` | Register `AGENT_INSTRUCTIONS.md` as harness_policy source |
| `core_memory/adapters/hermes.py` | Register Hermes-specific soul source (TBD path/format) |
