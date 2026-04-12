# Core Memory Skill Instructions For OpenClaw

Status: draft companion skill document

Purpose: provide operational instructions that reinforce the system prompt without conflicting with it.

This document is intentionally narrower than the system prompt.
It does not redefine authority or schema.
It tells an OpenClaw-facing skill how to behave while respecting Core Memory's canonical boundaries.

## Relationship To The System Prompt

The system prompt defines:
- ownership
- invariants
- canonical labels
- write/read/flush boundaries

This skill defines:
- which surface to call
- when to stay thin
- when to hydrate
- how to avoid bypassing canonical runtime logic

If this document conflicts with the system prompt or canonical docs, treat canonical docs as authoritative.

## Core Rule

OpenClaw is not the memory authority.
Core Memory is the memory authority.

The skill must:
- route finalized-turn writes through canonical ingestion
- route retrieval through canonical read surfaces
- avoid direct file mutation of memory internals

Never:
- write `.beads/index.json` directly
- write `.turns/*.jsonl` directly
- mutate rolling-window artifacts directly
- create archive/flush artifacts directly
- treat the bridge/plugin as the owner of memory semantics

## Write-Path Instructions

### 1. Use finalized-turn ingestion only

For top-level user turns, route memory writes through:
- `process_turn_finalized(...)` as the canonical runtime boundary

Adapter/helper ingress may use:
- `emit_turn_finalized(...)`
- OpenClaw `agent_end` bridge wiring

The skill should assume:
- the runtime owns idempotency
- the runtime owns `session_id` / `turn_id` dedupe
- the runtime owns persistence details

The skill's job is to provide a semantically faithful finalized turn payload.

### 2. Write exactly one current-turn bead payload

Per finalized turn, the semantic current-turn write should describe exactly one current-turn bead.

That bead may be:
- thin
- or rich

Thin bead:
- minimal temporal/chronology preservation
- usually not retrieval-eligible

Rich bead:
- structured retrieval fields
- claims when warranted
- evidence/quality signals
- stronger retrieval usefulness

Do not force a rich bead when the turn does not support one.

### 3. Prefer explicit semantic content over vague summary prose

When the turn supports them, prefer:
- `type`
- `title`
- `summary`
- `retrieval_title`
- `retrieval_facts`
- `supporting_facts`
- `evidence_refs`
- `because`
- `state_change`
- claims / claim updates

Do not add generic filler summary text just to make a bead look richer.

### 4. Claims should be grounded, not ornamental

Use claims when the turn expresses durable facts, preferences, policies, commitments, conditions, relationships, or locations.

Use claim updates when the turn or reviewed decision pass clearly supports:
- `reaffirm`
- `supersede`
- `retract`
- `conflict`

Do not fabricate claim updates just because a slot exists.
Do not duplicate timestamps inside claims when bead-level temporal grounding already covers them.

### 5. Associations must be selective

Always preserve temporal continuity via bead linkage fields when available:
- `prev_bead_id`
- `next_bead_id`

Append semantic associations only when there is real support.

Prefer canonical relations such as:
- `caused_by`
- `led_to`
- `supports`
- `contradicts`
- `supersedes`
- `resolves`

Do not add weak edges just to make the graph denser.
Do not use helper-only tags as durable semantic relations.

## Promotion Instructions

The skill may help provide evidence for promotion review, but runtime/store paths own actual lifecycle mutation.

Treat promotion as a judgment pass over visible session beads:
- `promoted`
- `candidate`
- `null`

Use durable signals such as:
- final decisions
- persistent preferences
- validated lessons
- critical incidents
- high-confidence structured evidence

Do not equate "recent" with "promotable."
Do not promote generic context beads unless their long-term value is clear.

Note:
- current repo behavior around promotion monotonicity is still in transition
- the skill should therefore recommend carefully, not assume it owns irreversible lifecycle changes

## Retrieval Instructions

### 1. Default to `memory.execute`

For most agent-facing recall, use:
- `memory.execute`

Why:
- it is the canonical end-to-end retrieval surface
- it handles claim state, ranking, answer policy, and grounded output

### 2. Use `memory.search` when the task is typed search

Use `memory.search` when you need:
- faceted search
- filter-heavy lookup
- debugging/explaining recall behavior
- exploratory search without full answer synthesis

### 3. Use `memory.trace` for causal questions

Use `memory.trace` when the user is asking:
- why something happened
- what led to something
- which chain explains an outcome

### 4. Hydrate only after selecting anchors

If deeper context is needed, first retrieve candidate beads.
Then use:
- `get_turn(...)`
- `hydrate_bead_sources(...)`
- adjacent-turn helpers only when explicitly needed

Do not hydrate full turns first and search second.

### 5. Respect same-session vs durable recall

For recent same-session context:
- transcript or adjacent-turn context may be useful

For durable or cross-session memory:
- prefer Core Memory retrieval surfaces

The skill should not collapse these two modes into one generic search habit.

## Retrieval Quality Instructions

Prefer beads with:
- specific `retrieval_title`
- concrete `retrieval_facts`
- evidence or supporting facts
- meaningful state change or supersession markers
- entity/topic/key grounding

If a bead is thin or generic:
- keep it useful for chronology
- do not overstate its retrieval authority

When memory is fuzzy:
- prefer a partial, grounded answer over false certainty

When memory lacks a credible anchor:
- abstain rather than hallucinate

## Temporal Instructions

When time matters:
- preserve available temporal metadata on the bead
- respect `effective_from` / `effective_to` / `observed_at` / `recorded_at` when present
- use `as_of` style retrieval or temporal-window retrieval when the question is historical

Do not answer historical questions as current truth.
Do not assume that older means historically correct unless time alignment is actually supported.

## Entity Instructions

Use canonical entities and aliases when available.

If the same thing is referred to in multiple ways, prefer consistent entity naming in:
- claims
- retrieval titles/facts
- associations

Do not create needless fragmentation by alternating labels for the same entity inside one turn.

## OpenClaw Bridge Instructions

The OpenClaw bridge should remain thin:
- extract finalized turn payload
- dedupe
- call canonical Core Memory ingestion
- return result

Do not move memory-engine logic into plugin JS or bridge adapters.
Do not make the bridge responsible for semantic storage policy.

## Operator-Facing Instructions

When validating system health, use:
- `core-memory metrics canonical-health`
- `core-memory metrics legacy-readiness`

If canonical health is not green:
- surface the failing check
- do not quietly treat degraded operation as normal

## Negative Instructions

Never:
- bypass canonical write/read surfaces
- mutate low-level memory files directly
- use weak fallback labels as a convenience
- invent associations to improve graph density
- treat bridge/runtime plumbing as semantic authorship
- over-hydrate turns before anchor selection
- confuse transcript convenience with durable memory authority
- claim certainty when only partial grounding exists

## Practical Retrieval Playbook

When the user asks "what do you remember / what is my current X":
- use `memory.execute`
- prefer claim-aware current-state answers

When the user asks "what changed / what was it before":
- use `memory.execute` with temporal intent and `as_of` or window constraints when possible

When the user asks "why / what led to this":
- use `memory.trace`

When results need explanation or filtering:
- use `memory.search`
- then hydrate selected anchors only if needed

## Final Principle

The skill should make OpenClaw feel operationally fluent without moving authority out of Core Memory.

System prompt:
- defines truth, ownership, and semantics

Skill:
- reinforces those rules by choosing the right surfaces in the right order
