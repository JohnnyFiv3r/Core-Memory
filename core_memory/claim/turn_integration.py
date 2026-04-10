"""
Turn-level claim extraction orchestrator.
Called after bead creation to extract and persist claims.
"""
from __future__ import annotations
from typing import Any

from core_memory.integrations.openclaw_flags import claim_layer_enabled, claim_extraction_mode
from core_memory.claim.extraction import extract_claims
from core_memory.claim.validation import validate_claims_batch, dedup_claims
from core_memory.persistence.store_claim_ops import write_claims_to_bead, resolve_current_state
from core_memory.claim.update_policy import emit_claim_updates


def extract_and_attach_claims(
    root: str,
    session_id: str,
    turn_id: str,
    created_bead_ids: list[str],
    req: dict,
) -> dict:
    """
    Extract claims from a turn and attach them to created beads.

    Args:
        root: Store root directory
        session_id: Current session ID
        turn_id: Current turn ID
        created_bead_ids: IDs of beads created this turn
        req: The original turn request dict (contains user_query, assistant_final, etc.)

    Returns:
        Telemetry dict: {claims_extracted, claims_written, bead_ids, updates_emitted}
    """
    if not claim_layer_enabled():
        return {"claims_extracted": 0, "claims_written": 0, "bead_ids": [], "updates_emitted": 0}

    mode = claim_extraction_mode()
    if mode == "off":
        return {"claims_extracted": 0, "claims_written": 0, "bead_ids": [], "updates_emitted": 0}

    user_query = req.get("user_query", "") or req.get("query", "") or ""
    assistant_final = req.get("assistant_final", "") or req.get("assistant_response", "") or ""
    context_beads = req.get("context_beads", [])

    # Extract claims (heuristic for now, LLM mode reserved)
    raw_claims = extract_claims(user_query, assistant_final, context_beads)
    valid_claims = validate_claims_batch(raw_claims)
    unique_claims = dedup_claims(valid_claims)

    claims_written = 0

    # Write claims to each created bead
    for bead_id in created_bead_ids:
        if unique_claims:
            write_claims_to_bead(root, bead_id, unique_claims)
            claims_written += len(unique_claims)

    # Emit claim updates based on existing state
    updates_emitted = 0
    if unique_claims and created_bead_ids:
        trigger_bead_id = created_bead_ids[-1]
        updates = emit_claim_updates(root, unique_claims, trigger_bead_id)
        updates_emitted = len(updates)

    return {
        "claims_extracted": len(raw_claims),
        "claims_written": claims_written,
        "bead_ids": created_bead_ids,
        "updates_emitted": updates_emitted,
    }
