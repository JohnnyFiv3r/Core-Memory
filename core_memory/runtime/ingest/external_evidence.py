"""Typed external memory ingest for source-attributed evidence.

This path writes semantic memory anchors, not raw source replicas. Source bodies,
document chunks, relational rows, and binary media remain in caller-owned stores
such as Ragie, Snowflake, Supabase, or another hydration backend.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.persistence import events
from core_memory.persistence.store import MemoryStore

TRANSCRIPT_FLAGS = {
    "transcript",
    "conversation.transcript",
    "conversation_transcript",
}
DOCUMENT_FLAGS = {
    "document",
    "media",
    "document.media",
    "document_media",
    "document/media",
    "document_reference",
    "media_reference",
}
RELATIONAL_FLAGS = {
    "relational",
    "relational.data",
    "relational_data",
    "structured",
    "structured_observation",
    "data_insight",
}
STATE_ASSERTION_FLAGS = {
    "state_assertion",
    "derived_business_state",
    "business_state",
    "document_claim",
    "document_observation",
}

EXTERNAL_BEAD_TYPES = {
    "transcript",
    "document_reference",
    "structured_observation",
    "state_assertion",
    "data_insight",
}


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _coerce_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _coerce_str_list(value: Any) -> list[str]:
    return [str(x).strip() for x in _coerce_list(value) if str(x).strip()]


def _coerce_summary(value: Any) -> list[str]:
    items = _coerce_str_list(value)
    return [x[:220] for x in items[:3]]


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _hydration_ref(payload: dict[str, Any]) -> dict[str, Any]:
    href = payload.get("hydration_ref")
    if isinstance(href, dict):
        store = _clean_str(href.get("store"))
        ref = _clean_str(href.get("ref"))
        out = {str(k): v for k, v in href.items() if str(k)}
        if store:
            out["store"] = store
        if ref:
            out["ref"] = ref
        return out
    store = _clean_str(payload.get("hydration_store"))
    ref = _clean_str(payload.get("hydration_ref") or payload.get("source_ref"))
    return {"store": store, "ref": ref} if store or ref else {}


def _normalize_flag(value: Any) -> str:
    return _clean_str(value).lower().replace(" ", "_")


def resolve_external_bead_type(payload: dict[str, Any]) -> str:
    explicit = _normalize_flag(payload.get("type"))
    if explicit in EXTERNAL_BEAD_TYPES:
        return explicit

    flag = _normalize_flag(payload.get("data_type_flag") or payload.get("source_kind"))
    if flag in TRANSCRIPT_FLAGS:
        return "transcript"
    if flag in DOCUMENT_FLAGS:
        return "document_reference"
    if flag in RELATIONAL_FLAGS:
        return "structured_observation"
    if flag in STATE_ASSERTION_FLAGS:
        return "state_assertion"

    source_kind = _normalize_flag(payload.get("source_kind"))
    if source_kind in {"document", "media"}:
        return "document_reference"
    if source_kind in {"relational", "structured"}:
        return "structured_observation"
    if source_kind == "transcript":
        return "transcript"
    if source_kind in {"derived", "analysis"}:
        return "state_assertion"

    raise ValueError("external_evidence: missing_or_unknown_data_type_flag")


def _require(payload: dict[str, Any], fields: tuple[str, ...]) -> None:
    missing = []
    for field in fields:
        value = payload.get(field)
        if isinstance(value, dict):
            ok = bool(value)
        elif isinstance(value, (list, tuple)):
            ok = bool(value)
        else:
            ok = bool(_clean_str(value))
        if not ok:
            missing.append(field)
    if missing:
        raise ValueError("external_evidence: missing required fields: " + ", ".join(missing))


def _default_source_kind(bead_type: str, payload: dict[str, Any]) -> str:
    existing = _normalize_flag(payload.get("source_kind"))
    if existing:
        return existing
    if bead_type == "document_reference":
        flag = _normalize_flag(payload.get("data_type_flag"))
        return "media" if flag == "media" else "document"
    if bead_type == "structured_observation":
        return "relational"
    if bead_type == "state_assertion":
        return "derived"
    if bead_type == "transcript":
        return "transcript"
    return "external"


def _source_ref_payload(payload: dict[str, Any], *, source_kind: str, hydration_ref: dict[str, Any]) -> list[dict[str, Any]]:
    provided = payload.get("source_refs")
    if isinstance(provided, list) and provided:
        return [x for x in provided if isinstance(x, dict) or str(x).strip()]

    ref = _clean_str(payload.get("source_ref") or hydration_ref.get("ref"))
    source_ref: dict[str, Any] = {
        "source_id": _clean_str(payload.get("source_id")),
        "source_event_id": _clean_str(payload.get("source_event_id")),
        "source_system": _clean_str(payload.get("source_system")),
        "source_kind": source_kind,
    }
    if ref:
        source_ref["ref"] = ref
    if hydration_ref:
        source_ref["hydration_ref"] = dict(hydration_ref)
    return [{k: v for k, v in source_ref.items() if v}]


def _source_attribution(payload: dict[str, Any], *, source_kind: str, hydration_ref: dict[str, Any]) -> dict[str, Any]:
    base = payload.get("source_attribution") if isinstance(payload.get("source_attribution"), dict) else {}
    out = dict(base or {})
    out.update({
        "source_id": _clean_str(payload.get("source_id")),
        "source_event_id": _clean_str(payload.get("source_event_id")),
        "source_system": _clean_str(payload.get("source_system")),
        "source_kind": source_kind,
        "core_memory_unifying_id": _clean_str(payload.get("core_memory_unifying_id")),
    })
    if hydration_ref:
        out["hydration_ref"] = dict(hydration_ref)
    return {k: v for k, v in out.items() if v}


def _find_existing_external_bead(store: MemoryStore, payload: dict[str, Any], bead_type: str) -> dict[str, Any] | None:
    source_event_id = _clean_str(payload.get("source_event_id"))
    source_id = _clean_str(payload.get("source_id"))
    source_record_id = _clean_str(payload.get("source_record_id"))
    document_id = _clean_str(payload.get("document_id") or payload.get("ragie_document_id"))
    transcript_id = _clean_str(payload.get("transcript_id") or payload.get("conversation_id"))

    index = store._read_json(store.beads_dir / "index.json")
    for bead in (index.get("beads") or {}).values():
        if _clean_str(bead.get("type")) != bead_type:
            continue
        if source_event_id and _clean_str(bead.get("source_event_id")) == source_event_id:
            return dict(bead)
        if source_id and source_record_id:
            if _clean_str(bead.get("source_id")) == source_id and _clean_str(bead.get("source_record_id")) == source_record_id:
                return dict(bead)
        if source_id and document_id:
            if _clean_str(bead.get("source_id")) == source_id and document_id in {
                _clean_str(bead.get("document_id")),
                _clean_str(bead.get("ragie_document_id")),
            }:
                return dict(bead)
        if source_id and transcript_id:
            if _clean_str(bead.get("source_id")) == source_id and transcript_id in {
                _clean_str(bead.get("transcript_id")),
                _clean_str(bead.get("conversation_id")),
            }:
                return dict(bead)
    return None


def _typed_fields(bead_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if bead_type == "document_reference":
        return {
            "document_id": _clean_str(payload.get("document_id")),
            "raw_source_object_id": _clean_str(payload.get("raw_source_object_id")),
            "ragie_document_id": _clean_str(payload.get("ragie_document_id")),
            "document_name": _clean_str(payload.get("document_name") or payload.get("title")),
            "mime_type": _clean_str(payload.get("mime_type")),
            "document_kind": _clean_str(payload.get("document_kind")),
            "document_date": _clean_str(payload.get("document_date")),
            "author_or_owner": _clean_str(payload.get("author_or_owner")),
            "section_refs": _coerce_list(payload.get("section_refs")),
        }
    if bead_type in {"structured_observation", "data_insight"}:
        return {
            "source_table": _clean_str(payload.get("source_table")),
            "source_record_id": _clean_str(payload.get("source_record_id")),
            "record_action": _clean_str(payload.get("record_action")),
            "record_grain": _clean_str(payload.get("record_grain")),
            "business_object_type": _clean_str(payload.get("business_object_type")),
            "business_object_id": _clean_str(payload.get("business_object_id")),
            "metric_name": _clean_str(payload.get("metric_name")),
            "metric_value": _coerce_float(payload.get("metric_value")),
            "metric_unit": _clean_str(payload.get("metric_unit")),
            "change_pct": _coerce_float(payload.get("change_pct")),
            "currency": _clean_str(payload.get("currency")),
            "as_of_timestamp": _clean_str(payload.get("as_of_timestamp")),
            "entity_refs": _coerce_str_list(payload.get("entity_refs")),
            "attribute_tags": _coerce_str_list(payload.get("attribute_tags")),
        }
    if bead_type == "state_assertion":
        return {
            "derived_from": _coerce_str_list(payload.get("derived_from")),
            "derived_from_bead_ids": _coerce_str_list(payload.get("derived_from_bead_ids")),
            "assertion_kind": _clean_str(payload.get("assertion_kind") or "business_state"),
            "assertion_subject": _clean_str(payload.get("assertion_subject")),
            "assertion_predicate": _clean_str(payload.get("assertion_predicate")),
            "assertion_value": _clean_str(payload.get("assertion_value")),
        }
    if bead_type == "transcript":
        return {
            "transcript_id": _clean_str(payload.get("transcript_id")),
            "conversation_id": _clean_str(payload.get("conversation_id")),
            "source_thread_id": _clean_str(payload.get("source_thread_id")),
            "source_session_id": _clean_str(payload.get("source_session_id")),
            "message_refs": _coerce_list(payload.get("message_refs")),
            "speaker_refs": _coerce_str_list(payload.get("speaker_refs")),
        }
    return {}


def _validate_external_payload(bead_type: str, payload: dict[str, Any], hydration_ref: dict[str, Any]) -> None:
    _require(payload, ("title", "summary"))
    if bead_type != "state_assertion":
        _require(payload, ("source_id", "source_event_id", "source_system", "core_memory_unifying_id"))
    if bead_type != "state_assertion" and not hydration_ref:
        raise ValueError("external_evidence: missing required fields: hydration_ref")

    if bead_type == "document_reference":
        if not _clean_str(payload.get("document_id") or payload.get("ragie_document_id")):
            raise ValueError("external_evidence: document_reference requires document_id or ragie_document_id")
        _require(payload, ("document_name",))
    elif bead_type in {"structured_observation", "data_insight"}:
        _require(payload, ("source_table", "source_record_id"))
        if not _clean_str(payload.get("as_of_timestamp") or payload.get("observed_at")):
            raise ValueError("external_evidence: structured_observation requires as_of_timestamp or observed_at")
        if not (_coerce_str_list(payload.get("entities")) or _coerce_str_list(payload.get("entity_refs"))):
            raise ValueError("external_evidence: structured_observation requires entities or entity_refs")
    elif bead_type == "transcript":
        if not (_coerce_list(payload.get("message_refs")) or _coerce_str_list(payload.get("source_turn_ids"))):
            raise ValueError("external_evidence: transcript requires message_refs or source_turn_ids")
    elif bead_type == "state_assertion":
        if not (_coerce_str_list(payload.get("derived_from")) or _coerce_str_list(payload.get("derived_from_bead_ids")) or _coerce_list(payload.get("evidence_refs"))):
            raise ValueError("external_evidence: state_assertion requires derived_from, derived_from_bead_ids, or evidence_refs")
        if not _clean_str(payload.get("effective_from") or payload.get("observed_at")):
            raise ValueError("external_evidence: state_assertion requires effective_from or observed_at")


def _bead_payload(payload: dict[str, Any], *, bead_type: str, source_kind: str, hydration_ref: dict[str, Any]) -> dict[str, Any]:
    summary = _coerce_summary(payload.get("summary"))
    entities = _coerce_str_list(payload.get("entities") or payload.get("entity_refs"))
    topics = _coerce_str_list(payload.get("topics"))
    attribute_tags = _coerce_str_list(payload.get("attribute_tags"))
    tags = _coerce_str_list(payload.get("tags"))
    for tag in ("external_evidence", source_kind, bead_type, _clean_str(payload.get("source_system"))):
        if tag and tag not in tags:
            tags.append(tag)
    for tag in attribute_tags:
        if tag not in tags:
            tags.append(tag)

    observed_at = _clean_str(payload.get("observed_at") or payload.get("as_of_timestamp"))
    recorded_at = _clean_str(payload.get("recorded_at")) or datetime.now(timezone.utc).isoformat()
    source_refs = _source_ref_payload(payload, source_kind=source_kind, hydration_ref=hydration_ref)

    out = {
        "type": bead_type,
        "title": _clean_str(payload.get("title"))[:200],
        "summary": summary,
        "detail": _clean_str(payload.get("detail") or payload.get("content"))[:1200],
        "session_id": _clean_str(payload.get("session_id")) or None,
        "source_turn_ids": _coerce_str_list(payload.get("source_turn_ids")),
        "authority": _clean_str(payload.get("authority")) or ("derived_analysis" if bead_type == "state_assertion" else "source_attributed"),
        "confidence": _coerce_float(payload.get("confidence")) if payload.get("confidence") is not None else 0.8,
        "tags": tags[:32],
        "entities": entities[:32],
        "topics": topics[:32],
        "supporting_facts": _coerce_str_list(payload.get("supporting_facts")),
        "evidence_refs": _coerce_list(payload.get("evidence_refs")),
        "observed_at": observed_at or None,
        "recorded_at": recorded_at,
        "effective_from": _clean_str(payload.get("effective_from")) or None,
        "effective_to": _clean_str(payload.get("effective_to")) or None,
        "data_type_flag": _clean_str(payload.get("data_type_flag")) or source_kind,
        "source_id": _clean_str(payload.get("source_id")),
        "source_event_id": _clean_str(payload.get("source_event_id")),
        "source_system": _clean_str(payload.get("source_system")),
        "source_kind": source_kind,
        "source_ref": _clean_str(payload.get("source_ref") or hydration_ref.get("ref")),
        "source_refs": source_refs,
        "source_attribution": _source_attribution(payload, source_kind=source_kind, hydration_ref=hydration_ref),
        "core_memory_unifying_id": _clean_str(payload.get("core_memory_unifying_id")),
        "hydration_ref": hydration_ref,
    }
    out.update(_typed_fields(bead_type, payload))
    return {k: v for k, v in out.items() if v is not None}


def ingest_external_evidence(root: str, payload: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    """Write a typed external source bead and return an ingest receipt."""
    if not isinstance(payload, dict):
        raise ValueError("external_evidence: payload must be an object")

    merged = dict(payload)
    if session_id and not _clean_str(merged.get("session_id")):
        merged["session_id"] = session_id

    bead_type = resolve_external_bead_type(merged)
    source_kind = _default_source_kind(bead_type, merged)
    hydration_ref = _hydration_ref(merged)
    _validate_external_payload(bead_type, merged, hydration_ref)

    store = MemoryStore(root=root)
    existing = _find_existing_external_bead(store, merged, bead_type)
    if existing:
        bead_id = _clean_str(existing.get("id"))
        return {
            "ok": True,
            "accepted": True,
            "status": "already_exists",
            "mode": "external_evidence",
            "bead_id": bead_id,
            "bead_ids": [bead_id] if bead_id else [],
            "created_count": 0,
            "event_id": "",
            "type": bead_type,
            "source_event_id": _clean_str(merged.get("source_event_id")),
            "core_memory_unifying_id": _clean_str(merged.get("core_memory_unifying_id")),
        }

    bead = _bead_payload(merged, bead_type=bead_type, source_kind=source_kind, hydration_ref=hydration_ref)
    bead_id = store.add_bead(
        type=bead.pop("type"),
        title=bead.pop("title"),
        summary=bead.pop("summary"),
        detail=bead.pop("detail", ""),
        session_id=bead.pop("session_id", None),
        source_turn_ids=bead.pop("source_turn_ids", []),
        tags=bead.pop("tags", []),
        **bead,
    )
    event_id = events.append_event(
        Path(root),
        _clean_str(merged.get("session_id")) or session_id,
        "external_evidence_ingested",
        {
            "bead_id": bead_id,
            "type": bead_type,
            "source_id": _clean_str(merged.get("source_id")),
            "source_event_id": _clean_str(merged.get("source_event_id")),
            "source_system": _clean_str(merged.get("source_system")),
            "source_kind": source_kind,
            "core_memory_unifying_id": _clean_str(merged.get("core_memory_unifying_id")),
        },
    )
    return {
        "ok": True,
        "accepted": True,
        "status": "accepted",
        "mode": "external_evidence",
        "bead_id": bead_id,
        "bead_ids": [bead_id],
        "created_count": 1,
        "event_id": event_id,
        "type": bead_type,
        "source_event_id": _clean_str(merged.get("source_event_id")),
        "core_memory_unifying_id": _clean_str(merged.get("core_memory_unifying_id")),
    }


def ingest_structured_observation(root: str, payload: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    merged = dict(payload or {})
    merged.setdefault("type", "structured_observation")
    merged.setdefault("data_type_flag", "relational")
    return ingest_external_evidence(root, merged, session_id=session_id)


def ingest_document_reference(root: str, payload: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    merged = dict(payload or {})
    merged.setdefault("type", "document_reference")
    merged.setdefault("data_type_flag", "document")
    return ingest_external_evidence(root, merged, session_id=session_id)


def ingest_state_assertion(root: str, payload: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    merged = dict(payload or {})
    merged.setdefault("type", "state_assertion")
    merged.setdefault("data_type_flag", "state_assertion")
    return ingest_external_evidence(root, merged, session_id=session_id)


__all__ = [
    "ingest_external_evidence",
    "ingest_structured_observation",
    "ingest_document_reference",
    "ingest_state_assertion",
    "resolve_external_bead_type",
]
