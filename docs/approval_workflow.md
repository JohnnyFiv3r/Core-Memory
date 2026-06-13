# Approval Workflow (Human-in-the-Loop)

A unified review gate over the bead store. Where `confirm` is a lightweight
one-shot "I vouch for this," **approval** is the full workflow: a bead can be
flagged `pending`, then `approved` (a human signs off) or `rejected` (a human
deems it not memory-worthy). This is the review structure a Satorid-fed system
needs — operational systems auto-write beads, and approval is the gate for the
subset that needs human judgment.

Beads are immutable records. Approval changes governance state and lifecycle
status, never content.

## States

`approval_status` on a bead is one of:

| Status | Meaning | Retrieval effect | Confidence effect |
|---|---|---|---|
| *(absent)* | not in a review workflow | normal | none |
| `pending` | awaiting human review | **still retrievable** (a signal, not a gate) | none |
| `approved` | a human signed off | normal | raises to **A**, `authority=user_confirmed` |
| `rejected` | deemed not memory-worthy | **excluded** from current truth (retained for audit) | n/a (archived) |

Two deliberate choices:

- **Pending beads stay retrievable.** Approval is a trust signal layered on top
  of grounding and confidence class, not a hard quarantine. Hard-gating every
  auto-written bead until a human clicks approve would make memory useless until
  the queue is drained. The queue exists to surface what needs review; rejection
  is the only state that removes.
- **Rejection is not provenance history.** Superseded versions are surfaced via
  `include_superseded` because they were once true. A rejected bead was judged
  *not memory-worthy*, so it is excluded from retrieval unconditionally — but it
  stays in the index (with the rejecter and reason) for audit.

Approving a `speculative` bead lifts its grounding to `inferred` (a human has
grounded it), so confidence class A is consistent with the speculative ceiling.

## How a bead enters review

1. Explicitly: `request_approval(bead_id)` sets `approval_status=pending`.
2. At write time: a connector sets `approval_status: "pending"` on the payload
   (e.g. Satorid flags low-confidence or policy-sensitive auto-captures).

## Operations across every surface

| Operation | Python | HTTP | MCP tool |
|---|---|---|---|
| request review | `request_approval(root, bead_id, requested_by, note)` | `POST /v1/memory/request-approval` | `request_memory_approval` |
| approve | `approve_bead(root, bead_id, approver, note)` | `POST /v1/memory/approve` | `approve_memory` |
| reject | `reject_bead(root, bead_id, approver, reason)` | `POST /v1/memory/reject` | `reject_memory` |
| list queue | `list_pending_approvals(root, limit)` | `GET /v1/memory/pending-approvals` | `list_pending_approvals` |

Store methods mirror these: `MemoryStore.request_approval/approve/reject/pending_approvals`.
PydanticAI agents get the same four as tools via `memory_approval_tools(root)`.
Each op emits an event (`bead_approval_requested` / `bead_approved` /
`bead_rejected`) and writes a full bead snapshot to the session archive so
`rebuild_index()` preserves the record.

## Relationship to `confirm`

`confirm` and `approve` both grant class A and set `authority=user_confirmed`.
`approve` additionally records the approver, tracks the `pending → approved`
transition, and has a symmetric `reject`. Use `confirm` for a direct vouch with
no review workflow; use the approval surface when there is a queue and a
reviewer. `confirm` remains for backward compatibility.

## Relationship to other decision queues

Approval is the general gate for *beads*. The promotion slate
(`decide_promotion`), entity-merge proposals (`decide_entity_merge_proposal`),
and Dreamer candidates (`apply_reviewed_proposal`) remain their own typed
decision queues for their specific objects — approval does not replace them.
