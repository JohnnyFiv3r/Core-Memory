"""
Turn-level claim extraction orchestrator.
Called after bead creation to extract and persist claims on the canonical turn bead.
"""
from __future__ import annotations

from core_memory.claim.extraction import extract_claims
from core_memory.claim.validation import dedup_claims, validate_claims_batch
from core_memory.integrations.openclaw_flags import claim_extraction_mode, claim_layer_enabled
from core_memory.persistence.store_claim_ops import find_canonical_turn_bead_id, write_claims_to_bead


def extract_and_attach_claims(
    root: str,
    session_id: str,
    turn_id: str,
    created_bead_ids: list[str],
    req: dict,
) -> dict:
    """
    Extract claims from a turn and attach them to the canonical turn bead only.

    Returns telemetry:
    - claims_extracted
    - claims_written
    - bead_ids
    - canonical_bead_id
    - claims_batch (internal handoff for decision-pass update policy)
    """
    canonical_bead_id = find_canonical_turn_bead_id(
        root,
        session_id=str(session_id),
        turn_id=str(turn_id),
        preferred_bead_ids=list(created_bead_ids or []),
    )

    if not claim_layer_enabled():
        return {
            "claims_extracted": 0,
            "claims_written": 0,
            "bead_ids": [canonical_bead_id] if canonical_bead_id else [],
            "canonical_bead_id": canonical_bead_id,
            "claims_batch": [],
            "updates_emitted": 0,
        }

    mode = claim_extraction_mode()
    if mode == "off":
        return {
            "claims_extracted": 0,
            "claims_written": 0,
            "bead_ids": [canonical_bead_id] if canonical_bead_id else [],
            "canonical_bead_id": canonical_bead_id,
            "claims_batch": [],
            "updates_emitted": 0,
        }

    user_query = req.get("user_query", "") or req.get("query", "") or ""
    assistant_final = req.get("assistant_final", "") or req.get("assistant_response", "") or ""
    context_beads = req.get("context_beads", [])

    raw_claims = extract_claims(user_query, assistant_final, context_beads)
    valid_claims = validate_claims_batch(raw_claims)
    unique_claims = dedup_claims(valid_claims)

    claims_written = 0
    if canonical_bead_id and unique_claims:
        write_claims_to_bead(root, canonical_bead_id, unique_claims)
        claims_written = len(unique_claims)

    return {
        "claims_extracted": len(raw_claims),
        "claims_written": claims_written,
        "bead_ids": [canonical_bead_id] if canonical_bead_id else [],
        "canonical_bead_id": canonical_bead_id,
        "claims_batch": unique_claims,
        "updates_emitted": 0,
    }
