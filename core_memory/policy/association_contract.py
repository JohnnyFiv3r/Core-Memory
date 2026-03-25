from __future__ import annotations

from typing import Any


RELATION_ALIASES = {
    # methodology variants
    "enables": "enables",
    "causes": "caused_by",
    "caused_by": "caused_by",
    "refines": "refines",
    "supports": "supports",
    "blocks→unblocks": "blocks_unblocks",
    "blocks->unblocks": "blocks_unblocks",
    "blocks_unblocks": "blocks_unblocks",
    "invalidates": "invalidates",
    "diagnoses": "diagnoses",
    # existing/legacy normalizations
    "led_to": "led_to",
    "blocked_by": "blocked_by",
    "unblocks": "unblocks",
    "supersedes": "supersedes",
    "superseded_by": "superseded_by",
    "associated_with": "associated_with",
    "contradicts": "contradicts",
    "reinforces": "reinforces",
    "mirrors": "mirrors",
    "applies_pattern_of": "applies_pattern_of",
    "violates_pattern_of": "violates_pattern_of",
    "constraint_transformed_into": "constraint_transformed_into",
    "solves_same_mechanism": "solves_same_mechanism",
    "similar_pattern": "similar_pattern",
    "transferable_lesson": "transferable_lesson",
    "generalizes": "generalizes",
    "specializes": "specializes",
    "structural_symmetry": "structural_symmetry",
    "reveals_bias": "reveals_bias",
    "derived_from": "derived_from",
    "resolves": "resolves",
    "follows": "follows",
    "precedes": "precedes",
}


def normalize_assoc_row(row: dict[str, Any]) -> dict[str, Any]:
    """Canonical association row normalization for policy layer."""
    rel_raw = str(row.get("relationship") or "").strip().lower()
    rel = RELATION_ALIASES.get(rel_raw, rel_raw)
    return {
        "source_bead_id": str(row.get("source_bead_id") or row.get("source_bead") or "").strip(),
        "target_bead_id": str(row.get("target_bead_id") or row.get("target_bead") or "").strip(),
        "relationship": rel,
        "confidence": row.get("confidence"),
        "rationale": row.get("rationale"),
    }


def assoc_row_is_valid(row: dict[str, Any], *, allowed_ids: set[str] | None = None) -> bool:
    src = str(row.get("source_bead_id") or "").strip()
    tgt = str(row.get("target_bead_id") or "").strip()
    rel = str(row.get("relationship") or "").strip().lower()
    if not src or not tgt or not rel:
        return False
    if allowed_ids is not None and (src not in allowed_ids or tgt not in allowed_ids):
        return False
    return True


def assoc_dedupe_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("source_bead_id") or "").strip(),
        str(row.get("target_bead_id") or "").strip(),
        str(row.get("relationship") or "").strip().lower(),
    )
