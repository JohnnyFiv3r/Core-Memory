"""
Current-state resolver for claims across the full store.
Groups claims by subject+slot, applies updates, returns current best state.
"""
from __future__ import annotations

from core_memory.claim.resolver_helpers import is_claim_current, find_conflicts, build_claim_timeline
from core_memory.persistence.store_claim_ops import read_all_claim_rows
from core_memory.temporal import claim_visible_as_of, normalize_as_of, claim_temporal_sort_key


def _load_all_claims_and_updates(root: str) -> tuple[list[dict], list[dict]]:
    """Load all claims and updates from canonical bead-embedded storage."""
    return read_all_claim_rows(root)


def resolve_all_current_state(root: str, session_id: str | None = None, as_of: str | None = None) -> dict:
    """
    Resolve current state for all subject+slot combinations in the store.

    Args:
        root: Store root directory
        session_id: Optional session filter (not yet implemented — reserved for future)

    Returns:
        {
            slots: {
                "<subject>:<slot>": {
                    current_claim: dict | None,
                    history: list[dict],
                    conflicts: list[dict],
                    timeline: list[dict],
                    status: str,  # active | retracted | conflict | not_found
                }
            },
            total_slots: int,
            active_slots: int,
            conflict_slots: int,
        }
    """
    all_claims, all_updates = _load_all_claims_and_updates(root)
    as_of_dt = normalize_as_of(as_of)

    # Group claims by subject+slot
    slot_claims: dict[str, list[dict]] = {}
    for claim in all_claims:
        subject = claim.get("subject", "")
        slot = claim.get("slot", "")
        if not subject or not slot:
            continue
        key = f"{subject}:{slot}"
        slot_claims.setdefault(key, []).append(claim)

    # Group updates by subject+slot
    slot_updates: dict[str, list[dict]] = {}
    for update in all_updates:
        subject = update.get("subject", "")
        slot = update.get("slot", "")
        if not subject or not slot:
            continue
        key = f"{subject}:{slot}"
        slot_updates.setdefault(key, []).append(update)

    result_slots = {}
    active_count = 0
    conflict_count = 0

    for key, claims in slot_claims.items():
        updates = slot_updates.get(key, [])

        # Find current claims (not superseded or retracted)
        visible_claims = [c for c in claims if claim_visible_as_of(c, as_of_dt)]
        visible_claims = sorted(visible_claims, key=claim_temporal_sort_key)

        current_claims = [c for c in visible_claims if is_claim_current(c, updates, as_of=as_of)]
        conflicts = find_conflicts(visible_claims, updates, as_of=as_of)
        timeline = build_claim_timeline(visible_claims, updates, as_of=as_of)

        current_claim = current_claims[-1] if current_claims else None

        if conflicts:
            status = "conflict"
            conflict_count += 1
        elif current_claim:
            status = "active"
            active_count += 1
        else:
            status = "retracted"

        result_slots[key] = {
            "current_claim": current_claim,
            "history": visible_claims,
            "conflicts": conflicts,
            "timeline": timeline,
            "status": status,
        }

    return {
        "slots": result_slots,
        "total_slots": len(result_slots),
        "active_slots": active_count,
        "conflict_slots": conflict_count,
        "as_of": str(as_of or "") or None,
    }
