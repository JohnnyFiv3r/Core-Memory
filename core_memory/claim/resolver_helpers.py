"""Helper functions for claim state resolution."""
from __future__ import annotations

from core_memory.temporal import claim_visible_as_of, normalize_as_of, update_visible_as_of, claim_temporal_sort_key


def is_claim_current(claim: dict, updates: list[dict], *, as_of: str | None = None) -> bool:
    """
    Returns True if the claim is not superseded or retracted by any update.
    """
    as_of_dt = normalize_as_of(as_of)
    if not claim_visible_as_of(claim, as_of_dt):
        return False

    claim_id = claim.get("id")
    for update in updates:
        if not update_visible_as_of(update, as_of_dt):
            continue
        decision = update.get("decision", "")
        target = update.get("target_claim_id")
        if target == claim_id and decision in ("supersede", "retract"):
            return False
    return True


def find_conflicts(claims: list[dict], updates: list[dict], *, as_of: str | None = None) -> list[dict]:
    """
    Returns claims that have a 'conflict' update targeting them.
    """
    as_of_dt = normalize_as_of(as_of)
    conflict_ids = {
        u.get("target_claim_id")
        for u in updates
        if u.get("decision") == "conflict" and update_visible_as_of(u, as_of_dt)
    }
    return [c for c in claims if c.get("id") in conflict_ids and claim_visible_as_of(c, as_of_dt)]


def build_claim_timeline(claims: list[dict], updates: list[dict], *, as_of: str | None = None) -> list[dict]:
    """
    Build a timeline of claim events sorted by confidence then order.
    Returns list of {event_type, claim, update} dicts.
    """
    timeline = []

    as_of_dt = normalize_as_of(as_of)

    # Map claim IDs to claims
    claim_map = {c.get("id"): c for c in claims}

    # Start: each claim is an 'assert' event
    for claim in sorted(list(claims or []), key=claim_temporal_sort_key):
        if not claim_visible_as_of(claim, as_of_dt):
            continue
        timeline.append({
            "event_type": "assert",
            "claim": claim,
            "update": None,
        })

    # Each update is a modification event
    for update in updates:
        if not update_visible_as_of(update, as_of_dt):
            continue
        target_id = update.get("target_claim_id")
        timeline.append({
            "event_type": update.get("decision", "unknown"),
            "claim": claim_map.get(target_id),
            "update": update,
        })

    return timeline
