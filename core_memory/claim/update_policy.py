"""
Per-turn claim update emission.
Scans existing claims and emits ClaimUpdates when new claims supersede, retract, or conflict.
"""
from __future__ import annotations
import uuid
from pathlib import Path
import json

from core_memory.persistence.store_claim_ops import resolve_current_state, write_claim_updates_to_bead


def emit_claim_updates(root: str, new_claims: list[dict], trigger_bead_id: str) -> list[dict]:
    """
    For each new claim, check if there is an existing current claim for same subject+slot.
    If so, emit a 'supersede' ClaimUpdate targeting the old claim.

    Args:
        root: Store root directory
        new_claims: Newly extracted claims for this turn
        trigger_bead_id: The bead that triggered these updates

    Returns:
        List of emitted ClaimUpdate dicts
    """
    emitted = []

    for claim in new_claims:
        subject = claim.get("subject", "")
        slot = claim.get("slot", "")
        new_id = claim.get("id", "")

        if not subject or not slot:
            continue

        # Check existing state
        state = resolve_current_state(root, subject, slot)
        existing = state.get("current_claim")

        if existing and existing.get("id") != new_id:
            # New claim supersedes existing
            update = {
                "id": str(uuid.uuid4()),
                "decision": "supersede",
                "target_claim_id": existing["id"],
                "replacement_claim_id": new_id,
                "subject": subject,
                "slot": slot,
                "reason_text": f"New claim extracted in turn supersedes prior claim.",
                "trigger_bead_id": trigger_bead_id,
                "confidence": claim.get("confidence", 0.6),
            }
            write_claim_updates_to_bead(root, trigger_bead_id, [update])
            emitted.append(update)

    return emitted
