# Temporal Truth Contract

Status: canonical contract (TT-1)

Purpose: define one unambiguous temporal semantics model for Core Memory truth resolution and retrieval behavior.

## Temporal Axes

Core Memory uses two temporal perspectives:

- **transaction time**: when memory was written into canonical store surfaces
- **valid time**: when memory is considered true in the represented world

These must not be conflated.

## Field Semantics

### `recorded_at`

- Transaction-time marker.
- Meaning: when a row/bead/event was recorded by Core Memory.
- Source of truth for ingestion chronology and replay/debug timelines.
- Does **not** by itself mean the statement was true at that moment in the represented world.

### `observed_at`

- Observation-time marker.
- Meaning: when the source observation occurred (if distinct from recording).
- Used when ingesting delayed telemetry or retrospective reports.

### `effective_from`

- Valid-time interval start.
- Meaning: earliest time this memory/claim should be considered valid.
- If omitted, valid-time start defaults to best available anchor:
  1. `observed_at`
  2. `recorded_at`
  3. bead `created_at`

### `effective_to`

- Valid-time interval end (exclusive by contract).
- Meaning: first time at which this memory/claim should no longer be considered valid.
- If omitted, validity is open-ended until superseded/retracted/closed by later evidence.

### Query-time `as_of`

- Read-time selector for valid-time truth.
- Meaning: return the best memory state that was valid at `as_of`.
- If `as_of` omitted, resolve current-state semantics.

## Current-State Resolution

For `subject + slot` style truth:

1. gather relevant historical claims and updates
2. remove retracted/superseded rows for the chosen temporal window
3. apply valid-time filtering (`effective_from <= as_of < effective_to` when bounds exist)
4. choose strongest surviving candidate (confidence + recency + update chain integrity)
5. if unresolved conflict remains, mark conflict state and avoid overconfident current answers

## Historical Resolution

When user intent is historical (`as_of`, "last week", "previously", etc.):

- resolver must select valid truth for that historical point/interval
- answer policy should prefer `answer_historical` when evidence is strong but non-current
- responses must avoid presenting historical truth as present truth

## Determinism Rules

- Interval comparisons are deterministic and timezone-normalized (UTC contract at store boundary).
- `effective_to` is exclusive.
- Missing/invalid temporal fields do not crash retrieval; they degrade with explicit warnings.

## Scope Constraints (First Pass)

- Do not add duplicate claim-local temporal fields unless existing canonical timing surfaces prove insufficient.
- Prefer existing canonical fields and write surfaces.
- Keep canonical authority in existing store/runtime boundaries.

## Interaction With Supersession/Contradiction

- `supersede` updates and explicit `effective_to` both close prior validity windows.
- contradiction markers do not auto-resolve truth; they force conflict-aware ranking/answer policy.

## Required Behavior Guarantees

- Current and historical queries can resolve differently against same slot when data supports it.
- Resolver and answer policy remain explainable (`as_of`, selected interval, conflict penalties, etc.).
