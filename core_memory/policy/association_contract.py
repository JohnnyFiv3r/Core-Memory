from __future__ import annotations

from typing import Any

from core_memory.schema.normalization import canonicalize_association_edge


def normalize_assoc_row(row: dict[str, Any]) -> dict[str, Any]:
    """Canonical association row normalization for policy layer."""
    rel_raw = str(row.get("relationship") or "").strip().lower()
    edge = canonicalize_association_edge(
        row.get("source_bead_id") or row.get("source_bead"),
        row.get("target_bead_id") or row.get("target_bead"),
        rel_raw,
    )
    return {
        "source_bead_id": str(edge.get("source_bead") or "").strip(),
        "target_bead_id": str(edge.get("target_bead") or "").strip(),
        "relationship": str(edge.get("relationship") or "").strip(),
        "confidence": row.get("confidence"),
        "rationale": row.get("rationale"),
        "relationship_raw": rel_raw if edge.get("normalization_applied") else row.get("relationship_raw"),
        "endpoints_swapped": bool(edge.get("endpoints_swapped")),
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
