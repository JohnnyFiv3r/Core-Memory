"""Goal Lifecycle v2 — the shared goal-state dependency (SOUL PRD §6.0).

Today a Goal Bead supports only ``candidate -> resolved`` (outcome-matched, in
``promotion_service.resolve_goal_candidate_for_store``). This module adds the
broader lifecycle named in the SOUL / Dreamer / Myelination PRDs:

    candidate ─▶ endorsed ─▶ active ─▶ completed
        │           │          │
        ├──────────▶├─────────▶├──▶ abandoned
        └──────────▶└─────────▶└──▶ decaying ──▶ (endorsed | active | abandoned)

``completed``, ``abandoned`` and the legacy ``resolved`` are terminal. A Goal
Bead is authoritative (§6.1): valid transitions come from human declaration /
approval / completion / abandonment, or a guarded governance decision — never
from Dreamer inference (which only proposes candidates).

State lives on the Goal Bead's ``goal_status`` (terminal states also close
``status``), so the existing index-annotation model and audit log are reused.
This is a projection/annotation on the bead record, not new bead content.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..schema.promotion_contract import current_promotion_state
from ..retrieval.lifecycle import mark_semantic_dirty
from .io_utils import append_jsonl, store_lock
from .store_index_heads_ops import read_heads_for_store, write_heads_for_store

# Canonical goal lifecycle states.
GOAL_STATES: frozenset[str] = frozenset(
    {"candidate", "endorsed", "active", "completed", "abandoned", "decaying", "resolved"}
)

# Terminal states have no outgoing transitions.
TERMINAL_GOAL_STATES: frozenset[str] = frozenset({"completed", "abandoned", "resolved"})

# States that close the bead's `status` field (so status-based filters catch them).
_CLOSING_STATES = {"completed", "abandoned"}

# Allowed transitions. `resolved` is reached only via the outcome-match path
# (resolve_goal_candidate_for_store), so it is not a target here.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "candidate": frozenset({"endorsed", "abandoned", "decaying"}),
    "endorsed": frozenset({"active", "completed", "abandoned", "decaying"}),
    "active": frozenset({"completed", "abandoned", "decaying"}),
    "decaying": frozenset({"endorsed", "active", "abandoned"}),
}

# Targets a caller may request through this function (resolved/candidate excluded).
TRANSITIONABLE_TARGETS: frozenset[str] = frozenset(
    {"endorsed", "active", "completed", "abandoned", "decaying"}
)


def current_goal_status(bead: dict[str, Any]) -> str:
    """Canonical lifecycle state of a Goal Bead.

    Prefers an explicit ``goal_status``; otherwise maps legacy resolved encodings
    to ``resolved`` and everything else (open/candidate/unset) to ``candidate``.
    """
    gs = str(bead.get("goal_status") or "").strip().lower()
    if gs in GOAL_STATES:
        return gs
    # Legacy resolved encodings: a closed status, OR a raw promotion_state of
    # "resolved" (current_promotion_state() normalizes that to "null", so check
    # the raw field directly — mirrors is_active_goal's terminal handling).
    status = str(bead.get("status") or "").strip().lower()
    raw_promotion_state = str(bead.get("promotion_state") or "").strip().lower()
    if status == "resolved" or raw_promotion_state == "resolved" or current_promotion_state(bead) == "resolved":
        return "resolved"
    return "candidate"


def transition_goal_state_for_store(
    store: Any,
    *,
    goal_bead_id: str,
    to_state: str,
    reason: str = "",
    actor: str = "",
    turn_id: str = "",
) -> dict[str, Any]:
    """Apply a validated Goal Lifecycle v2 transition on a Goal Bead.

    Returns ``{ok, bead_id, from_state, to_state}`` on success, or
    ``{ok: False, error}`` for an unknown bead, non-goal, terminal source, or a
    transition the state machine disallows. Writes are serialized under the store
    lock and appended to the promotion-decision audit log.
    """
    gid = str(goal_bead_id or "").strip()
    target = str(to_state or "").strip().lower()
    if not gid:
        return {"ok": False, "error": "missing_goal_bead_id"}
    if target not in TRANSITIONABLE_TARGETS:
        return {"ok": False, "error": "invalid_target_state", "to_state": target,
                "allowed_targets": sorted(TRANSITIONABLE_TARGETS)}

    with store_lock(store.root):
        index = store._read_json(store.beads_dir / "index.json")
        bead = (index.get("beads") or {}).get(gid)
        if not isinstance(bead, dict):
            return {"ok": False, "error": f"bead_not_found:{gid}"}
        if str(bead.get("type") or "").strip().lower() != "goal":
            return {"ok": False, "error": "not_goal", "bead_id": gid}

        from_state = current_goal_status(bead)
        if from_state in TERMINAL_GOAL_STATES:
            return {"ok": False, "error": "goal_terminal", "bead_id": gid, "from_state": from_state}
        if target == from_state:
            return {"ok": True, "bead_id": gid, "from_state": from_state, "to_state": target, "noop": True}
        if target not in _ALLOWED_TRANSITIONS.get(from_state, frozenset()):
            return {
                "ok": False,
                "error": "invalid_transition",
                "bead_id": gid,
                "from_state": from_state,
                "to_state": target,
                "allowed": sorted(_ALLOWED_TRANSITIONS.get(from_state, frozenset())),
            }

        now = datetime.now(timezone.utc).isoformat()
        bead["goal_status"] = target
        bead["goal_state_changed_at"] = now
        if reason:
            bead["goal_state_reason"] = str(reason)
        if actor:
            bead["goal_state_actor"] = str(actor)
        if turn_id:
            bead["goal_state_turn_id"] = str(turn_id)
        if target in _CLOSING_STATES:
            bead["status"] = target
            bead["promotion_locked"] = True
            bead[f"{target}_at"] = now

        index["beads"][gid] = bead
        store._write_json(store.beads_dir / "index.json", index)

        # Keep the goal head cache (.beads/heads.json, read by
        # `core-memory heads --goal-id`) in sync so it doesn't report stale
        # status until an unrelated rewrite. Update the existing head's status in
        # place (preserving its bead_id pointer); create it if absent.
        goal_id = str(bead.get("goal_id") or "").strip()
        if goal_id:
            heads = read_heads_for_store(store)
            goals_head = heads.setdefault("goals", {})
            existing = goals_head.get(goal_id) if isinstance(goals_head.get(goal_id), dict) else None
            goals_head[goal_id] = {
                "bead_id": str((existing or {}).get("bead_id") or gid),
                "goal_status": target,
                "updated_at": now,
            }
            write_heads_for_store(store, heads)

        append_jsonl(
            store.beads_dir / "events" / "promotion-decisions.jsonl",
            {
                "ts": now,
                "bead_id": gid,
                "before_status": from_state,
                "after_status": target,
                "decision": f"goal_{target}",
                "reason": str(reason or ""),
                "actor": str(actor or ""),
                "turn_id": str(turn_id or ""),
            },
        )
        mark_semantic_dirty(store.root, reason=f"goal_lifecycle_{target}")

    return {"ok": True, "bead_id": gid, "from_state": from_state, "to_state": target}


__all__ = [
    "GOAL_STATES",
    "TERMINAL_GOAL_STATES",
    "TRANSITIONABLE_TARGETS",
    "current_goal_status",
    "transition_goal_state_for_store",
]
