"""Turn-level claim advisory and authored-claim telemetry.

Canonical claims belong in ``agent_authored_updates.v1`` and are persisted with
their bead.  This module may still produce deterministic or model-assisted
*advisories*, but it must not silently turn those candidates into claim truth.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.claim.extraction import extract_claims
from core_memory.claim.validation import dedup_claims, validate_claims_batch
from core_memory.config.feature_flags import claim_extraction_mode, claim_layer_enabled
from core_memory.persistence.io_utils import append_jsonl
from core_memory.persistence.store_claim_ops import (
    find_canonical_turn_bead_id,
    read_claims_for_bead,
)


_DELEGATED_CLAIM_SOURCES = {"inline_agent", "delegated_semantic_agent", "repair_agent"}


def _empty(*, canonical_bead_id: str = "", authored_claims: int = 0) -> dict[str, Any]:
    return {
        "claims_extracted": 0,
        "claims_written": 0,
        "authored_claims": authored_claims,
        "advisory_claims": [],
        "bead_ids": [canonical_bead_id] if canonical_bead_id else [],
        "canonical_bead_id": canonical_bead_id,
        "claims_batch": [],
        "updates_emitted": 0,
    }


def _append_advisory_claims(
    root: str,
    *,
    session_id: str,
    turn_id: str,
    bead_id: str,
    source: str,
    claims: list[dict[str, Any]],
) -> None:
    if not claims:
        return
    append_jsonl(
        Path(root) / ".beads" / "events" / "claim-advisories.jsonl",
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": "claim_advisory",
            "source": source,
            "session_id": str(session_id),
            "turn_id": str(turn_id),
            "bead_id": str(bead_id),
            "claims": claims,
            "canonical": False,
        },
    )


def extract_and_attach_claims(
    root: str,
    session_id: str,
    turn_id: str,
    created_bead_ids: list[str],
    req: dict,
) -> dict:
    """Return persisted authored claims plus optional non-canonical advice.

    The historical name is retained as a compatibility facade.  In particular,
    the ``heuristic`` mode no longer calls ``write_claims_to_bead``; it emits a
    durable, explicitly non-canonical advisory record for repair/review.
    """
    canonical_bead_id = find_canonical_turn_bead_id(
        root,
        session_id=str(session_id),
        turn_id=str(turn_id),
        preferred_bead_ids=list(created_bead_ids or []),
    )
    persisted = read_claims_for_bead(root, canonical_bead_id) if canonical_bead_id else []
    authored_claims = len(persisted)
    result = _empty(canonical_bead_id=canonical_bead_id, authored_claims=authored_claims)

    # Authored bead claims have already been accepted by the typed turn
    # contract.  Feature flags govern optional advice, not their persistence.
    if not claim_layer_enabled() or claim_extraction_mode() == "off":
        result["claims_batch"] = list(persisted)
        return result

    mode = claim_extraction_mode()
    user_query = req.get("user_query", "") or req.get("query", "") or ""
    assistant_final = req.get("assistant_final", "") or req.get("assistant_response", "") or ""
    context_beads = req.get("context_beads", [])
    authorship = dict(req.get("authorship") or req.get("authorship_provenance") or {})
    source = str(authorship.get("source") or "").strip()

    if mode == "llm":
        # A model result is authoritative only when it arrived in the full
        # typed authoring contract.  Standalone legacy judge rows remain advice.
        raw_claims = list(req.get("_judged_claims") or [])
        advisory_source = "delegated_claim_advisory"
        if source in _DELEGATED_CLAIM_SOURCES and persisted:
            result["claims_batch"] = list(persisted)
            result["delegated_authorship_source"] = source
            return result
    else:
        raw_claims = extract_claims(user_query, assistant_final, context_beads)
        advisory_source = "heuristic_claim_extractor"

    advisory = dedup_claims(validate_claims_batch(raw_claims))
    _append_advisory_claims(
        root,
        session_id=session_id,
        turn_id=turn_id,
        bead_id=canonical_bead_id,
        source=advisory_source,
        claims=advisory,
    )
    result.update(
        {
            "claims_extracted": len(raw_claims),
            "advisory_claims": advisory,
            "claims_batch": list(persisted),
            "advisory_only": True,
        }
    )
    return result
