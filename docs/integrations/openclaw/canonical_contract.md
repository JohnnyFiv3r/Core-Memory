# Canonical Contract

This document is the single source of truth for what must never break in Core Memory.

It defines the canonical ownership responsibilities for:
- bead authorship and fact attribution
- claim and claim-update authorship
- promotion-state judgment
- association creation
- intent-driven retrieval

Core Memory has one canonical write authority and one canonical family of read surfaces.

Canonical runtime authority:
- turn path: `process_turn_finalized(...)`
- flush path: `process_flush(...)`

Canonical read surfaces:
- `memory.execute`
- `memory.search`
- `memory.trace`
- `get_turn(...)`
- `hydrate_bead_sources(...)`

Adapters may include:
- OpenClaw
- PydanticAI
- SpringAI / HTTP
- LangChain

Adapters are not the memory authority.
They are host/runtime ingress and retrieval paths that must route through Core Memory's canonical surfaces.

The adapter or bridge layer must remain thin:
- extract finalized turn payload
- dedupe
- call canonical Core Memory ingestion
- return result

Never move memory-engine logic into:
- plugin JS
- adapter wrappers
- direct file mutation of `.beads`, `.turns`, rolling-window, or archive artifacts

## Runtime guarantees

The following behaviors happen automatically at runtime.
You must be aware of them, but you must not attempt to simulate or replace them yourself:

- idempotent turn ingestion by `(session_id, turn_id)`
- runtime id and timestamp generation
- canonical turn persistence
- archive compaction
- rolling-window maintenance
- flush checkpointing and replay-skip behavior
- semantic index rebuild / refresh triggers
- canonical-health and legacy-readiness reporting

Your job is semantic authorship and retrieval discipline:
- describe the turn faithfully
- author strong semantic fields when warranted
- author claims and claim updates when justified
- create only justified associations
- use the correct retrieval surface for the question

## A) Per-turn path (finalized turn / adapter ingress)

For every finalized top-level user turn:

1. Route through canonical finalized-turn ingestion.
2. Provide exactly one semantically faithful current-turn bead payload.
3. Preserve temporal grounding when available.
4. When the turn states durable facts, decisions, preferences, policies, commitments, conditions, relationships, or locations, author structured claims on the current-turn bead.
5. When the turn or reviewed decision pass provides evidence that an earlier claim should be reaffirmed, superseded, retracted, or marked in conflict, author a claim update.
6. Populate continuity linkage fields such as `prev_bead_id` and `next_bead_id` when available.
7. Append semantic associations only when evidence supports them.
8. Evaluate promotion state for visible session beads as a preservation judgment: `promoted`, `candidate`, or `null`.
9. Set retrieval eligibility from payload quality rather than verbosity.

The runtime automatically:
- ensures the turn is processed once
- writes the canonical turn record
- persists the current-turn bead
- runs promotion-state decisioning
- updates indexes and projections

Do not describe these runtime guarantees as if they are file-level tasks you must perform yourself.

### Current-turn bead rule

Every finalized turn should yield exactly one semantic current-turn bead.

That bead may be:
- thin
- or rich

Thin bead:
- preserves chronology and replay
- is usually `retrieval_eligible=false`
- is valid and must not be forced into artificial richness

Rich bead:
- includes stronger retrieval fields
- may include claims
- may include stronger evidence and semantic structure

Do not force a rich bead when the turn does not support one.

### Condensed bead shape

You should understand the bead as a structured record with a few major groups of fields.

Minimal canonical bead shape:
- `id`
- `type`
- `title`
- `created_at`
- `session_id`
- `source_turn_ids`

Core semantic payload:
- `summary`
- `detail`
- `scope`
- `authority`
- `confidence`
- `tags`

Continuity and ordering:
- `turn_index`
- `prev_bead_id`
- `next_bead_id`

Retrieval richness:
- `retrieval_eligible`
- `retrieval_title`
- `retrieval_facts`
- `entities`
- `topics`
- `incident_keys`
- `decision_keys`
- `goal_keys`
- `action_keys`
- `outcome_keys`
- `time_keys`

Reasoning and evidence:
- `because`
- `supporting_facts`
- `evidence_refs`
- `state_change`

Temporal and supersession:
- `observed_at`
- `recorded_at`
- `effective_from`
- `effective_to`
- `validity`
- `supersedes`
- `superseded_by`

Claim layer:
- `claims`
- `claim_updates`
- `interaction_role`
- `memory_outcome`

Important interpretation rules:
- not every bead must populate every field
- thin vs rich is determined by field completeness and `retrieval_eligible`, not by bead type
- runtime owns ids, timestamps, and persistence details
- semantic authorship owns the meaning-bearing fields
- linkage fields such as `prev_bead_id` and `next_bead_id` are bead fields, not association rows
- claims and claim updates live on the bead, but they are not required on every turn

### Claims

Claims are first-class semantic state authored on the canonical current-turn bead.

Use claims when the turn expresses durable state such as:
- preferences
- identity or profile facts
- policies and standing instructions
- commitments or next-step obligations
- conditions that affect behavior
- relationships
- locations

Each claim should be grounded in observable turn content.

Claims should include:
- `claim_kind`
- `subject`
- `slot`
- `value`
- `reason_text`
- `confidence`

Do not create claims for:
- pure conversational filler
- weak hunches
- generic acknowledgements
- thin or meta-only runtime chatter

### Claim updates

Claim updates are later judgments about earlier claims.
They are not generic extraction byproducts.

Use claim updates only when there is evidence for:
- `reaffirm`
- `supersede`
- `retract`
- `conflict`

Do not assume claim updates are required on every claim-bearing turn.
Use them when the turn or reviewed decision pass actually supports a change in claim validity.

### Associations

Bead linkage fields and association rows are not the same thing.

Continuity linkage fields:
- `prev_bead_id`
- `next_bead_id`
- session linkage
- turn ordering fields

These preserve chronology and replay continuity.

Association rows are separate semantic edges between beads.

Use semantic associations only when the relation is justified by evidence.
Common canonical relations include:
- `caused_by`
- `led_to`
- `supports`
- `contradicts`
- `supersedes`
- `resolves`
- `follows`
- `precedes`

Never invent weak associations to make the graph look complete.
Never use helper-only tags as durable semantic relations.

### Promotion state

Promotion is a preservation judgment over visible session beads.

Use:
- `promoted`
- `candidate`
- `null`

Promotable content typically includes:
- final decisions
- validated lessons
- persistent preferences
- critical incidents
- durable policy or high-value state

Do not equate:
- recency
- verbosity
- rich formatting

with promotability.

Treat promotion as a strong, sticky long-term judgment.
Do not change it casually.

Current implementation note you must respect:
- the runtime still contains a rebalance path that can demote weakly supported promotions

So do not behave as if promotion is fully irreversible unless the runtime has been updated to make that true.

### Retrieval eligibility

Retrieval eligibility is a quality policy, not a brittle field checklist.

A bead is a strong candidate for `retrieval_eligible=true` when it has:
- a non-generic title
- a useful `retrieval_title`
- concrete `retrieval_facts`
- at least one quality signal such as:
 - `because`
 - `supporting_facts`
 - `state_change`
 - `evidence_refs`
 - `supersedes`
 - `superseded_by`

If those are weak or generic:
- prefer a thin bead
- leave retrieval eligibility off

Do not inflate weak beads just to satisfy a checklist.

## B) Flush path (memory flush cycle only)

The flush path is a runtime-owned lifecycle, separate from semantic turn authorship.

Runtime-owned flush sequence:
1. archive compaction (session scope)
2. rolling-window maintenance write
3. archive compaction (historical scope)
4. flush checkpoint/report

You must be aware that this lifecycle exists, but you must not attempt to perform those file-level operations directly.

Because flush is runtime-owned:
- do not write archive artifacts directly
- do not mutate rolling-window files directly
- do not simulate checkpointing behavior in output

## C) Rolling window maintenance

The rolling window is:
- a continuity or injection surface
- a token-budget management surface
- a derived surface

It is not the primary truth authority for semantic storage.

The rolling window is updated during the flush cycle.
It should never be treated as the primary semantic write authority on the turn path.

## D) Archive ergonomics

Archive is:
- the durable long-range retrieval authority for preserved memory
- replayable
- allowed to retain richer historical context than the rolling window

When a bead is archived:
- it is not deleted
- it is not wrong
- it is not missing

If full detail is needed, recover it through canonical retrieval and hydration surfaces rather than assuming loss.

## E) Full retrieval path

Use the current canonical retrieval family, not legacy or invented surfaces.

### Default read surface

Use `memory.execute` for end-to-end grounded recall.

This is the default choice for:
- "what do you remember"
- "what is my current X"
- "what changed"
- claim-aware answer generation

### Typed or faceted search

Use `memory.search` when you need:
- filter-heavy lookup
- exploratory search
- typed search debugging
- faceted candidate selection without full answer synthesis

### Causal tracing

Use `memory.trace` for:
- why questions
- what led to this questions
- causal chain inspection

### Hydration

Use:
- `get_turn(...)`
- `hydrate_bead_sources(...)`

only after anchors have been selected.

Do not hydrate full turn sources first and search second.

### Source hierarchy guidance

For recent same-session context:
- transcript or adjacent-turn context may help

For durable or cross-session recall:
- prefer canonical Core Memory retrieval surfaces

Do not collapse transcript convenience and durable memory authority into one undifferentiated read habit.

### Retrieval quality rule

Retrieval quality depends on structured memory, not generic prose.

Prefer beads with:
- specific `retrieval_title`
- concrete `retrieval_facts`
- evidence or supporting facts
- meaningful state-change or supersession markers
- grounded entities, topics, or keys

If memory is fuzzy:
- prefer a partial grounded answer over false certainty

If memory lacks a credible anchor:
- abstain rather than hallucinate

## F) Operator checks

Preserve awareness of the primary operational checks:
- `core-memory metrics canonical-health`
- `core-memory metrics legacy-readiness`

Expected posture:
- canonical health should be green before release
- legacy-readiness should monitor fallback and shim exposure

These are runtime and operator concerns.
You should know they exist and matter, but you should not pretend to compute them manually.

## G) Canonical definitions

Every label below should be understood positively and negatively:
- what it means
- when to use it
- what it does not mean

### Semantic bead types

`goal`
- desired future state, target, or requirement
- not a realized result

`decision`
- durable choice or committed path
- not brainstorming or loose consideration

`tool_call`
- tool action whose execution materially matters
- not every trivial command

`evidence`
- proof, logs, metrics, or artifacts
- not speculation or interpretation alone

`outcome`
- realized result or consequence
- not intended result

`lesson`
- reusable takeaway learned from experience
- not a one-off observation

`checkpoint`
- progress marker preserved for continuation
- not every ordinary turn

`precedent`
- prior case reused as a pattern or example
- not generic historical mention

`failed_hypothesis`
- tested explanation or approach shown wrong
- not an open question

`reversal`
- explicit overturning of an earlier stance or decision
- not a small correction

`misjudgment`
- mistake caused by flawed reasoning or prioritization
- not a neutral factual correction

`overfitted_pattern`
- misleading or too-narrow generalization
- not any ordinary failed idea

`abandoned_path`
- intentionally dropped line of work
- not a disproven hypothesis

`reflection`
- retrospective evaluation of process or approach
- not a durable design rule

`design_principle`
- reusable architectural or design rule
- not a one-off project preference

`context`
- situational framing, background, or working-state information that helps interpret nearby turns
- not a safe default bucket for durable semantics

`correction`
- fix to a mistaken understanding
- not a full course reversal

System-only boundary records such as `session_start` and `session_end` are not ordinary semantic bead types you should select during turn interpretation.

### Claim kinds

`preference`
- stable or semi-stable likes, dislikes, defaults, or favored ways of working
- not one-off situational choices

`identity`
- stable fact about who someone is or profile-like metadata
- not temporary conditions

`policy`
- rule, guardrail, or standing operating instruction
- not a temporary state

`commitment`
- promised or intended future action
- not a vague wish

`condition`
- current condition affecting interpretation or behavior
- not stable identity or long-term policy

`relationship`
- durable relationship between people, systems, teams, or entities
- not event-level coincidence

`location`
- place-based or environment-based durable fact
- not abstract scope

`custom`
- durable claim that does not fit the tighter canonical kinds
- not a lazy escape hatch when a clearer kind fits

### Claim update decisions

`reaffirm`
- later evidence confirms the claim remains valid

`supersede`
- later evidence replaces the claim with a newer one

`retract`
- later evidence invalidates the claim without a clean replacement

`conflict`
- later evidence creates unresolved contradiction against the claim

### Retrieval intents

`remember`
- direct recall of what is known, decided, preferred, or established

`causal`
- explanation, mechanism, or rationale

`what_changed`
- updates, replacements, corrections, or differences over time

`when`
- timeline, ordering, date, or as-of retrieval

`other`
- rare internal fallback only

### Relation disambiguation

`caused_by`
- source happened because target created the condition or mechanism
- not loose influence

`led_to`
- source contributed forward into target as a downstream consequence
- not backward explanation

`supports`
- source meaningfully supports target
- not repeated-pattern reinforcement

`reinforces`
- source strengthens confidence in target through repeated or independent agreement
- not one-off support

`contradicts`
- source and target cannot both stand as written
- not minor nuance

`invalidates`
- source makes target no longer valid
- not clean replacement

`supersedes`
- source replaces target as the newer or current version
- not unresolved disagreement

`associated_with`
- meaningful relation exists, but no stronger relation is justified
- use only when a stronger relation cannot honestly be supported

## H) Negative instructions

Never:
- bypass canonical write or read surfaces
- mutate `.beads`, `.turns`, rolling-window, or archive files directly
- treat bridge or adapter plumbing as semantic authorship
- use `context` as a convenience dump bucket
- invent weak associations to make the graph denser
- overstate thin beads as strong retrieval authority
- answer historical questions as current truth
- claim certainty when only partial grounding exists
