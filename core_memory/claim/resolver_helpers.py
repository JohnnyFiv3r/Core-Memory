"""Helper functions for claim state resolution."""
from __future__ import annotations


def is_claim_current(claim: dict, updates: list[dict]) -> bool:
    """
    Returns True if the claim is not superseded or retracted by any update.
    """
    claim_id = claim.get("id")
    for update in updates:
        decision = update.get("decision", "")
        target = update.get("target_claim_id")
        if target == claim_id and decision in ("supersede", "retract"):
            return False
    return True


def find_conflicts(claims: list[dict], updates: list[dict]) -> list[dict]:
    """
    Returns claims that have a 'conflict' update targeting them.
    """
    conflict_ids = {
        u.get("target_claim_id")
        for u in updates
        if u.get("decision") == "conflict"
    }
    return [c for c in claims if c.get("id") in conflict_ids]


def build_claim_timeline(claims: list[dict], updates: list[dict]) -> list[dict]:
    """
    Build a timeline of claim events sorted by confidence then order.
    Returns list of {event_type, claim, update} dicts.
    """
    timeline = []

    # Map claim IDs to claims
    claim_map = {c.get("id"): c for c in claims}

    # Start: each claim is an 'assert' event
    for claim in claims:
        timeline.append({
            "event_type": "assert",
            "claim": claim,
            "update": None,
        })

    # Each update is a modification event
    for update in updates:
        target_id = update.get("target_claim_id")
        timeline.append({
            "event_type": update.get("decision", "unknown"),
            "claim": claim_map.get(target_id),
            "update": update,
        })

    return timeline
