# Claim Layer Contract

Status: Canonical

This document specifies the invariants and guarantees of the claim layer.

## Authority Rule

Claims are **always extracted from bead content, never fabricated**.

Corollary:
- No claim shall be inserted via API without source grounding
- Extraction must cite reason_text to bead content or inferred context
- Claims without source traceback are validation errors

## Claim Invariants by Kind

Each claim kind carries semantic invariants:

### preference
- **Scope**: User-asserted or inferred preference
- **Value type**: String or enum (e.g., "coffee", "UTC-8")
- **Updatable**: Yes (new preference supersedes old)
- **Example**: `user:beverage=coffee`

### identity
- **Scope**: Durable identity assertion
- **Value type**: String, role, or identifier
- **Updatable**: Yes (rare; usually retracted then re-asserted)
- **Invariant**: At most one active identity per subject per slot
- **Example**: `alice:role=backend_engineer`

### policy
- **Scope**: Organizational or system-level rule
- **Value type**: Rule string or structured constraint
- **Updatable**: Yes (via supersede or amendment)
- **Invariant**: Policy revisions are immutable history
- **Example**: `system:login_requirement=2fa_mandatory`

### commitment
- **Scope**: Time-bound promise or goal
- **Value type**: Statement or target
- **Updatable**: Yes (via reaffirm or retract if abandoned)
- **Invariant**: Trigger dates and deadlines are recorded separately
- **Example**: `team:ship_by=2026-05-15`

### condition
- **Scope**: Observable state or precondition
- **Value type**: String or enum (e.g., "degraded", "healthy")
- **Updatable**: Yes (frequently; reflects state)
- **Invariant**: Conditions are time-relative; old conditions may stale
- **Example**: `database:state=degraded`

### relationship
- **Scope**: Connection or link between entities
- **Value type**: Related entity ID or reference
- **Updatable**: Yes (via retract if link broken)
- **Invariant**: Links are directional (A→B ≠ B→A)
- **Example**: `project_a:blocked_by=project_b`

### location
- **Scope**: Physical or logical placement
- **Value type**: Address, room, region, etc.
- **Updatable**: Yes (objects move)
- **Invariant**: Locations are point-in-time; old locations become historical
- **Example**: `alice:office_location=room_301`

### custom
- **Scope**: Application-specific fact
- **Value type**: Any
- **Updatable**: Yes (application-defined)
- **Invariant**: Custom kinds must pass validation

## Update Decision Rules

All claim updates follow these rules:

### supersede
- **Precondition**: New claim exists
- **Effect**: Old claim marked superseded; history preserved
- **Required**: `target_claim_id`, `replacement_claim_id`
- **Invariant**: Exactly one claim supersedes another (no branching)
- **History**: Old claim remains in history; timeline shows supersession

### retract
- **Precondition**: Claim exists
- **Effect**: Claim marked retracted; permanently withdrawn
- **Required**: `target_claim_id`
- **Invariant**: Retraction is permanent; cannot be undone
- **History**: Retracted claim remains in history; status = "retracted"

### reaffirm
- **Precondition**: Claim exists
- **Effect**: Claim confidence boosted or refreshed
- **Required**: `target_claim_id`
- **Invariant**: Reaffirm does not change value; only confidence
- **History**: New reaffirm event recorded in timeline

### conflict
- **Precondition**: Multiple claims exist
- **Effect**: Both claims marked conflicted; resolver flags status
- **Required**: `target_claim_id`
- **Invariant**: Conflict is symmetric; both claims are marked
- **History**: Conflict event in both timelines

## Resolver Behavior

The resolver (`resolve_current_state`, `resolve_all_current_state`) follows these rules:

### Append-only
- No claims or updates are deleted
- Resolution is computed, not stored
- History is always complete

### Update Governance
- Updates govern state transitions
- A claim is "current" if:
  - It exists
  - AND it is not marked superseded (target of supersede update)
  - AND it is not marked retracted (target of retract update)
- Last non-superseded, non-retracted claim wins

### History Preservation
- All claims remain in history regardless of status
- All updates form complete audit trail
- Timeline view shows event sequence

### Conflict Marking
- A claim is "conflict" if a conflict update targets it
- Resolver marks status = "conflict" for both claims
- Multiple conflicts per slot are possible

### Status Codes

- **active**: Claim is current and no conflicts
- **retracted**: Claim has retract update; permanently withdrawn
- **conflict**: Claim has conflict update; both sides marked
- **not_found**: No claims exist for subject+slot

## Required Fields

All claims must include:

| Field | Type | Constraint |
|-------|------|-----------|
| `id` | str | Non-empty, unique within store |
| `claim_kind` | str | One of eight canonical kinds or custom |
| `subject` | str | Non-empty; entity being claimed about |
| `slot` | str | Non-empty; fact category |
| `value` | Any | May be None; must be JSON-serializable |
| `reason_text` | str | Non-empty; why this claim was extracted |
| `confidence` | float | 0.0-1.0 inclusive; default 0.8 |

All claim updates must include:

| Field | Type | Constraint |
|-------|------|-----------|
| `id` | str | Non-empty, unique within store |
| `decision` | str | One of: supersede, retract, reaffirm, conflict |
| `target_claim_id` | str | Non-empty; which claim this targets |
| `subject` | str | Non-empty; must match target claim |
| `slot` | str | Non-empty; must match target claim |
| `reason_text` | str | Non-empty; why this update was emitted |
| `confidence` | float | 0.0-1.0 inclusive |

Optional fields on updates:
- `replacement_claim_id` — Required for supersede; None for others
- `trigger_bead_id` — Recommended; bead that caused the update

## Validation Contract

The validation layer (`claim.validation`) must enforce:

1. **Uniqueness**: No two claims with same ID
2. **Field completeness**: All required fields present
3. **Kind validity**: claim_kind in canonical list or explicitly custom
4. **Decision validity**: decision in canonical enum
5. **Confidence bounds**: 0.0 <= confidence <= 1.0
6. **JSON compatibility**: All values are JSON-serializable

Deduplication rules:
- Identical claims (same subject, slot, value) are deduplicated before write
- Newest claim by confidence wins; older dropped

## Error Handling

Resolver errors are **non-fatal**:
- Malformed claims are skipped with logging
- Missing bead directories are treated as empty
- Invalid updates do not block resolution

Claim layer is **degraded-safe**:
- If claim layer disabled, retrieval still works
- Missing claims do not prevent core operation
- Claim state is strictly read-only in resolver

## Integration Points

### Turn-level extraction (`claim.turn_integration`)
- Called after bead creation
- Extracts claims from user_query + assistant_final
- Writes claims to created beads
- Emits claim updates if new claims supersede existing

### Answer policy (`claim.answer_policy`)
- Decides answer outcome based on current claim state
- Input: current_state from resolve_current_state
- Output: answer_current | answer_historical | answer_partial | abstain

### Retrieval planning (`claim.retrieval_planner`)
- Plans retrieval mode based on query + claim state
- Input: query string + current_state
- Output: fact_first | causal_first | temporal_first | mixed

## Performance Considerations

- **Resolution is O(beads)**: Scans all bead directories once per query
- **No indexing**: Claims are scanned linearly; clustering by subject+slot on read
- **I/O bound**: Dominates resolve time; consider caching for high-volume queries
- **Append-only**: No compaction or garbage collection (V1)

Future optimization points (V2+):
- Index claims by subject+slot on disk
- Lazy-load claims per session
- Background resolver cache

## Historical Notes

The claim layer separates **fact capture** (claims) from **state change** (updates) to:
- Preserve audit trail without mutation
- Support conflict detection and resolution
- Enable time-aware retrieval and answer policy
- Decouple extraction from interpretation
