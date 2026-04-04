from __future__ import annotations

import hashlib
import re
from typing import Any


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(x) for x in value]
    return [str(value)]


NODE_LABEL_MODE_BEAD_PLUS_TYPE = "bead_plus_type"
NODE_LABEL_MODE_TYPE_ONLY = "type_only"

EDGE_MODE_ASSOCIATED = "associated"
EDGE_MODE_TYPED = "typed"


def bead_to_node(
    bead: dict[str, Any],
    *,
    label_mode: str = NODE_LABEL_MODE_BEAD_PLUS_TYPE,
) -> dict[str, Any]:
    """Map a Core Memory bead into Neo4j node payload (projection-only)."""
    typ = str(bead.get("type") or "unknown").strip()
    type_label = _to_type_label(typ)
    authority = str(bead.get("authority") or bead.get("continuity_authority") or "").strip()
    props = {
        "bead_id": str(bead.get("id") or ""),
        "type": typ,
        "title": str(bead.get("title") or ""),
        "status": str(bead.get("status") or ""),
        "session_id": str(bead.get("session_id") or ""),
        "scope": str(bead.get("scope") or ""),
        "authority": authority,
        "created_at": str(bead.get("created_at") or ""),
        "updated_at": str(bead.get("updated_at") or ""),
        "retrieval_eligible": bool(bead.get("retrieval_eligible", False)),
        "promotion_marked": bool(bead.get("promotion_marked", False)),
        "confidence": float(bead.get("confidence") or 0.0),
        "tags": _as_list(bead.get("tags")),
        "topics": _as_list(bead.get("topics")),
        "entities": _as_list(bead.get("entities")),
        "source_turn_ids": _as_list(bead.get("source_turn_ids")),
        "summary": _as_list(bead.get("summary")),
        "detail": str(bead.get("detail") or ""),
        "because": _as_list(bead.get("because")),
        "retrieval_title": str(bead.get("retrieval_title") or ""),
        "retrieval_facts": _as_list(bead.get("retrieval_facts")),
        "incident_id": str(bead.get("incident_id") or ""),
        "validity": str(bead.get("validity") or ""),
        "effective_from": str(bead.get("effective_from") or ""),
        "effective_to": str(bead.get("effective_to") or ""),
    }
    labels = [type_label]
    if str(label_mode or NODE_LABEL_MODE_BEAD_PLUS_TYPE).strip().lower() != NODE_LABEL_MODE_TYPE_ONLY:
        labels = ["Bead", type_label]

    return {
        "labels": labels,
        "properties": props,
    }


def association_to_edge(
    assoc: dict[str, Any],
    *,
    edge_mode: str = EDGE_MODE_ASSOCIATED,
) -> dict[str, Any]:
    src = str(assoc.get("source_bead") or assoc.get("source_bead_id") or "")
    dst = str(assoc.get("target_bead") or assoc.get("target_bead_id") or "")
    relationship = str(assoc.get("relationship") or "associated_with").strip().lower() or "associated_with"
    aid = str(assoc.get("id") or assoc.get("association_id") or "")
    if not aid:
        aid = _stable_association_id(src=src, dst=dst, relationship=relationship)

    e_mode = str(edge_mode or EDGE_MODE_ASSOCIATED).strip().lower()
    rel_type = "ASSOCIATED"
    if e_mode == EDGE_MODE_TYPED:
        rel_type = _to_relationship_type(relationship)

    return {
        "type": rel_type,
        "start_bead_id": src,
        "end_bead_id": dst,
        "properties": {
            "association_id": aid,
            "relationship": relationship,
            "edge_class": str(assoc.get("edge_class") or ""),
            "confidence": float(assoc.get("confidence") or assoc.get("weight") or 0.0),
            "provenance": str(assoc.get("provenance") or ""),
            "reason_text": str(assoc.get("reason_text") or assoc.get("explanation") or ""),
            "relationship_raw": str(assoc.get("relationship_raw") or ""),
            "warnings": _as_list(assoc.get("warnings")),
            "reason_code": str(assoc.get("reason_code") or ""),
            "created_at": str(assoc.get("created_at") or ""),
            "dedupe_key": _stable_association_id(src=src, dst=dst, relationship=relationship),
        },
    }


def _to_type_label(bead_type: str) -> str:
    t = str(bead_type or "").strip()
    if not t:
        return "Unknown"
    parts = [p for p in t.replace("-", "_").split("_") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts) or "Unknown"


def _stable_association_id(*, src: str, dst: str, relationship: str) -> str:
    raw = f"{src}|{dst}|{relationship}".encode("utf-8")
    return f"assoc-{hashlib.sha1(raw).hexdigest()[:16]}"


def _to_relationship_type(relationship: str) -> str:
    rel = str(relationship or "associated_with").strip().lower()
    rel = re.sub(r"[^a-z0-9_]+", "_", rel)
    rel = re.sub(r"_+", "_", rel).strip("_")
    if not rel:
        rel = "associated_with"
    if rel[:1].isdigit():
        rel = f"r_{rel}"
    return rel.upper()
