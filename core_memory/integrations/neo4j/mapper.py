from __future__ import annotations

import hashlib
from typing import Any


def bead_to_node(bead: dict[str, Any]) -> dict[str, Any]:
    """Map a Core Memory bead into Neo4j node payload (projection-only)."""
    typ = str(bead.get("type") or "unknown").strip()
    type_label = _to_type_label(typ)
    props = {
        "bead_id": str(bead.get("id") or ""),
        "type": typ,
        "title": str(bead.get("title") or ""),
        "status": str(bead.get("status") or ""),
        "session_id": str(bead.get("session_id") or ""),
        "scope": str(bead.get("scope") or ""),
        "authority": str(bead.get("authority") or ""),
        "created_at": str(bead.get("created_at") or ""),
        "updated_at": str(bead.get("updated_at") or ""),
        "retrieval_eligible": bool(bead.get("retrieval_eligible", False)),
        "promotion_marked": bool(bead.get("promotion_marked", False)),
        "confidence": float(bead.get("confidence") or 0.0),
        "tags": [str(x) for x in (bead.get("tags") or [])],
        "topics": [str(x) for x in (bead.get("topics") or [])],
        "entities": [str(x) for x in (bead.get("entities") or [])],
        "source_turn_ids": [str(x) for x in (bead.get("source_turn_ids") or [])],
        "summary": [str(x) for x in (bead.get("summary") or [])],
        "detail": str(bead.get("detail") or ""),
        "because": [str(x) for x in (bead.get("because") or [])],
        "retrieval_title": str(bead.get("retrieval_title") or ""),
        "retrieval_facts": [str(x) for x in (bead.get("retrieval_facts") or [])],
        "incident_id": str(bead.get("incident_id") or ""),
        "validity": str(bead.get("validity") or ""),
        "effective_from": str(bead.get("effective_from") or ""),
        "effective_to": str(bead.get("effective_to") or ""),
    }
    return {
        "labels": ["Bead", type_label],
        "properties": props,
    }


def association_to_edge(assoc: dict[str, Any]) -> dict[str, Any]:
    src = str(assoc.get("source_bead") or assoc.get("source_bead_id") or "")
    dst = str(assoc.get("target_bead") or assoc.get("target_bead_id") or "")
    relationship = str(assoc.get("relationship") or "associated_with")
    aid = str(assoc.get("id") or assoc.get("association_id") or "")
    if not aid:
        aid = _stable_association_id(src=src, dst=dst, relationship=relationship)

    return {
        "type": "ASSOCIATED",
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
            "warnings": [str(x) for x in (assoc.get("warnings") or [])],
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
