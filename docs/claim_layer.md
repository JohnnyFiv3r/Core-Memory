# Claim Layer

Status: Canonical

The claim layer provides structured capture, update, and resolution of discrete user-stated or agent-inferred facts across the store. Claims enable memory to track slots like user preferences, identity assertions, policies, relationships, and other persistent state.

## Overview

A **claim** is a discrete fact with structure: subject, slot, value, and confidence. Claims are immutable once written. State changes are governed by **claim updates** (supersede, retract, reaffirm, conflict), which form an audit trail while the resolver computes current state.

Why the claim layer exists:
- **Truth surfaces**: Expose what the system knows with confidence grades
- **State tracking**: Follow preference changes, identity assertions, policy commitments over time
- **Update semantics**: Distinguish assertion from state change without mutation
- **Retrieval planning**: Guide search strategy based on live claim inventory

## Claim Model

A claim is defined by these required fields:

```python
@dataclass
class Claim:
    id: str                 # Unique claim identifier
    claim_kind: str         # Type of claim (see Claim Kinds below)
    subject: str            # Entity making or about (e.g. "user", "project", "API")
    slot: str               # Fact category (e.g. "beverage", "timezone", "status")
    value: Any              # The asserted value (e.g. "coffee", "UTC-8", "active")
    reason_text: str        # Why this claim exists (extracted or inferred)
    confidence: float       # 0.0 to 1.0, default 0.8
```

Each claim is write-once. To change a fact, emit a new claim and link it with a ClaimUpdate.

## Claim Kinds

Canonical claim kinds and their typical uses:

| Kind | Purpose | Example |
|------|---------|---------|
| **preference** | User stated or inferred preference | "user prefers coffee" |
| **identity** | Who/what something is | "Alice is a backend engineer" |
| **policy** | Stated rule or constraint | "Always require 2FA for production" |
| **commitment** | Promise or goal statement | "Commit to ship feature X by Friday" |
| **condition** | Observable state or precondition | "Database is in degraded state" |
| **relationship** | Connection or link between entities | "ProjectA blocked by ProjectB" |
| **location** | Where something is | "Meeting is in Room 301" |
| **custom** | Application-specific | User-defined kind |

## Update Decisions

When a claim changes, emit a `ClaimUpdate` with one of these decisions:

```python
@dataclass
class ClaimUpdate:
    id: str                     # Update identifier
    decision: str               # Decision type (see below)
    target_claim_id: str        # Which claim this update targets
    replacement_claim_id: str | None  # New claim if supersede
    subject: str                # Subject of the slot
    slot: str                   # Slot being updated
    reason_text: str            # Why this update happened
    trigger_bead_id: str | None # Bead that triggered the update
    confidence: float           # Confidence in the update (0.0-1.0)
```

Decision types:

- **supersede**: Old claim is replaced by a new one. Requires `replacement_claim_id`.
- **retract**: Old claim is withdrawn permanently. No replacement.
- **reaffirm**: Old claim is confirmed again (confidence boost).
- **conflict**: Old claim conflicts with another; both marked conflicted.

## Resolution

### resolve_current_state

Resolves the current active claim for a single subject+slot pair:

```python
result = resolve_current_state(root, subject="user", slot="beverage")
```

Returns:

```python
{
    "current_claim": {...},  # Active claim or None
    "history": [...],        # All claims for this slot
    "conflicts": [...],      # Claims marked conflicted
    "status": "active" | "retracted" | "conflict" | "not_found"
}
```

Process:
1. Scan all bead claim files for subject+slot.
2. Collect all matching claims and their updates.
3. Mark claims as superseded/retracted based on update decisions.
4. Return the last non-superseded/non-retracted claim as current.

### resolve_all_current_state

Resolves all subject+slot pairs in the entire store:

```python
result = resolve_all_current_state(root)
```

Returns:

```python
{
    "slots": {
        "<subject>:<slot>": {
            "current_claim": {...},
            "history": [...],
            "conflicts": [...],
            "timeline": [...],
            "status": "active" | "retracted" | "conflict" | "not_found"
        },
        ...
    },
    "total_slots": 5,
    "active_slots": 4,
    "conflict_slots": 0,
}
```

Process:
1. Load all claims and updates from all beads.
2. Group by subject+slot.
3. Apply resolution logic to each group.
4. Return slot map with counts.

## Retrieval Integration

The claim layer informs retrieval in two ways:

### Answer Policy

`claim.answer_policy.decide_answer_outcome()` determines how to answer a query given claim state:

- **answer_current**: High-confidence answer from active claims
- **answer_historical**: Answer from historical claims with staleness warning
- **answer_partial**: Grounded but incomplete answer
- **abstain**: No credible anchor; do not answer from memory

### Retrieval Mode Planner

`claim.retrieval_planner.plan_retrieval_mode()` steers search strategy based on query and available claims:

- **fact_first**: Exact match for factual queries (common for claims)
- **causal_first**: Causal reasoning for why/how questions
- **temporal_first**: Recent beads for time-sensitive queries
- **mixed**: General balanced approach

## Supersede Chains, Grounding, and Conflict Semantics

- **`chain_seq`** — claim updates carry a monotonic per-slot sequence number;
  resolution orders supersede chains by `chain_seq`, never by write order.
  A slot's chain is also surfaced as a **claim worldline** by
  `core_memory.derive_worldlines` (the chain *is* the fact's thread through time).
- **`grounding_hash`** — every claim row carries a deterministic grounding
  hash (`compute_claim_grounding_hash`); per-slot dedup in the update path
  rejects re-emission of the same grounded assertion (WARNING telemetry).
- **`both_valid` + `context_scope`** — when two conflicting claims are both
  true in different contexts, conflict review resolves with
  `resolution="both_valid"` and labels each claim with a `context_scope`.
  Claims with different scopes never count as contradictions
  (`claim/epistemic.py`); contradiction pressure is computed only within a
  scope.

## Feature Flags

Control claim layer behavior via environment variables
(`core_memory/config/feature_flags.py`):

- `CORE_MEMORY_CLAIM_LAYER` — enable/disable the entire claim layer (default off)
- `CORE_MEMORY_CLAIM_EXTRACTION_MODE` = `off` | `heuristic` | `llm` — extraction strategy (default `off`)
- `CORE_MEMORY_CLAIM_RESOLUTION` — enable resolver surfaces (default off)
- `CORE_MEMORY_CLAIM_RETRIEVAL_BOOST` — boost claim-anchored rows in retrieval (default off)

Set via environment or in code:

```python
os.environ["CORE_MEMORY_CLAIM_LAYER"] = "true"
os.environ["CORE_MEMORY_CLAIM_EXTRACTION_MODE"] = "heuristic"
```

## Module Map

Core claim layer files:

| File | Purpose |
|------|---------|
| `core_memory/schema/models.py` | Claim and ClaimUpdate dataclasses |
| `core_memory/claim/extraction.py` | Extract claims from text (heuristic) |
| `core_memory/claim/validation.py` | Validate and deduplicate claims |
| `core_memory/claim/resolver.py` | Resolve current state across store |
| `core_memory/claim/resolver_helpers.py` | Helper: is_claim_current, find_conflicts, build_claim_timeline |
| `core_memory/claim/update_policy.py` | Emit claim updates after turn |
| `core_memory/claim/turn_integration.py` | Extract and attach claims in turn flow |
| `core_memory/claim/answer_policy.py` | Decide answer outcome from claim state |
| `core_memory/claim/answer_signals.py` | Compute signals (anchor confidence, evidence, etc.) |
| `core_memory/claim/retrieval_planner.py` | Plan retrieval mode based on claims and query |
| `core_memory/claim/conflict_review.py` | Agent-reviewed conflict resolution (incl. `both_valid` + `context_scope`) |
| `core_memory/claim/epistemic.py` | Contradiction pressure / epistemic uncertainty (scope-aware) |
| `core_memory/claim/outcomes.py` | Memory-outcome classification written back to beads |
| `core_memory/persistence/store_claim_ops.py` | Low-level I/O: read/write claims and updates |

## Architectural Notes

- **Append-only store**: Claims are never deleted, only superseded. All changes are recorded as updates.
- **Bead-resident**: Claims live as `claims` and `claim_updates` arrays on the
  bead rows inside `.beads/index.json`. Per-bead sidecar files
  (`<bead_id>/claims.json`, `<bead_id>/claim_updates.json`) are a read-only
  legacy fallback — nothing writes them canonically.
- **Session-scoped**: Claim extraction can be filtered by session (reserved for future).
- **Confidence grades**: All claims carry 0.0-1.0 confidence; policy layer uses this to decide answer strategy.
- **Conflict detection**: Multiple non-reconciled claims for the same slot trigger conflict status.

## Example Usage

See `examples/claim_layer_demo.py` for a working example that:
1. Writes two claims (initial preference, then updated preference)
2. Emits a supersede update
3. Resolves current state
4. Resolves full store state

Run:
```bash
python examples/claim_layer_demo.py
```
