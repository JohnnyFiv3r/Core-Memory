"""Normalized source-ingest envelope helpers.

The envelope is provenance for a coherent import/capture boundary. It is not a
semantic association by itself; association coverage may use it as candidate
evidence and judge context, but active graph edges still require judge approval.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


SOURCE_INGEST_ENVELOPE_SCHEMA = "core_memory.source_ingest_envelope.v1"


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _clean_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items() if _clean_str(k) and v not in (None, "", [], {})}


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _clean_str(value)
        if text:
            return text
    return ""


def _source_object_id(payload: dict[str, Any], explicit: dict[str, Any]) -> str:
    return _first_text(
        explicit.get("source_object_id"),
        payload.get("source_object_id"),
        payload.get("raw_source_object_id"),
        payload.get("source_record_id"),
        payload.get("business_object_id"),
        payload.get("document_id"),
        payload.get("transcript_id"),
        payload.get("conversation_id"),
        payload.get("source_id"),
    )


def _infer_boundary_type(payload: dict[str, Any], *, bead_type: str = "", source_kind: str = "") -> str:
    explicit = _first_text(
        payload.get("boundary_type"),
        payload.get("source_boundary_type"),
        payload.get("ingest_boundary_type"),
    )
    if explicit:
        return explicit
    typ = _clean_str(bead_type or payload.get("type")).lower()
    kind = _clean_str(source_kind or payload.get("source_kind") or payload.get("data_type_flag")).lower()
    if typ == "document_reference" and kind == "media":
        return "MediaImported"
    if typ == "document_reference" or kind in {"document", "document.media"}:
        return "DocumentImported"
    if typ == "transcript" or kind == "transcript":
        return "TranscriptCaptured"
    if typ in {"structured_observation", "data_insight"} or kind in {"relational", "structured"}:
        return "StructuredDatasetImported"
    if typ == "operational_event" or kind == "operational":
        return "OperationalEventCaptured"
    if typ == "state_assertion" or kind in {"derived", "analysis", "state_assertion"}:
        return "StateAssertionCaptured"
    return "SourceObjectCaptured"


def _local_refs(payload: dict[str, Any], explicit: dict[str, Any]) -> dict[str, Any]:
    out = _clean_dict(explicit.get("local_refs"))
    mapping = {
        "section_refs": "section_refs",
        "source_turn_ids": "turn_refs",
        "message_refs": "message_refs",
        "source_record_id": "row_ref",
        "source_table": "table_ref",
        "chunk_refs": "chunk_refs",
        "span_refs": "span_refs",
        "frame_refs": "frame_refs",
        "order_refs": "order_refs",
    }
    for source_key, target_key in mapping.items():
        value = payload.get(source_key)
        if value in (None, "", [], {}):
            continue
        out.setdefault(target_key, value)
    return out


def normalize_source_ingest_envelope(
    payload: dict[str, Any],
    *,
    bead_type: str = "",
    source_kind: str = "",
    hydration_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a normalized source-ingest envelope for an external write.

    Callers may provide `source_ingest_envelope`, `source_envelope`, or
    `ingest_envelope`. Missing fields are derived from the external-evidence
    payload so existing connectors get a stable envelope without changing their
    request shape.
    """
    if not isinstance(payload, dict):
        payload = {}
    explicit = {}
    for key in ("source_ingest_envelope", "source_envelope", "ingest_envelope"):
        if isinstance(payload.get(key), dict):
            explicit = dict(payload.get(key) or {})
            break

    hydration = _clean_dict(explicit.get("hydration_ref")) or _clean_dict(hydration_ref) or _clean_dict(payload.get("hydration_ref"))
    hydration_refs = [
        ref for ref in _as_list(explicit.get("hydration_refs") or payload.get("hydration_refs"))
        if isinstance(ref, dict) and ref
    ]
    if hydration and hydration not in hydration_refs:
        hydration_refs.insert(0, hydration)

    source_type = _first_text(
        explicit.get("source_type"),
        payload.get("source_type"),
        source_kind,
        payload.get("source_kind"),
        payload.get("source_system"),
    )
    source_object_id = _source_object_id(payload, explicit)
    source_event_id = _first_text(explicit.get("source_event_id"), payload.get("source_event_id"))
    boundary_type = _first_text(explicit.get("boundary_type"), _infer_boundary_type(payload, bead_type=bead_type, source_kind=source_kind))
    ingest_batch_id = _first_text(
        explicit.get("ingest_batch_id"),
        payload.get("ingest_batch_id"),
        payload.get("batch_id"),
        source_event_id,
        source_object_id,
    )

    envelope: dict[str, Any] = {
        "schema": SOURCE_INGEST_ENVELOPE_SCHEMA,
        "boundary_type": boundary_type,
        "ingest_batch_id": ingest_batch_id,
        "tenant_id": _first_text(explicit.get("tenant_id"), payload.get("tenant_id"), payload.get("root_id")),
        "workspace_id": _first_text(explicit.get("workspace_id"), payload.get("workspace_id")),
        "source_type": source_type,
        "source_id": _first_text(explicit.get("source_id"), payload.get("source_id")),
        "source_system": _first_text(explicit.get("source_system"), payload.get("source_system")),
        "source_object_id": source_object_id,
        "source_version": _first_text(
            explicit.get("source_version"),
            payload.get("source_version"),
            payload.get("document_version"),
            payload.get("sha"),
            payload.get("content_hash"),
        ),
        "source_uri": _first_text(
            explicit.get("source_uri"),
            payload.get("source_uri"),
            payload.get("source_url"),
            payload.get("source_ref"),
            hydration.get("ref"),
        ),
        "source_event_id": source_event_id,
        "source_event_type": _first_text(explicit.get("source_event_type"), payload.get("source_event_type"), payload.get("record_action")),
        "actor_id": _first_text(explicit.get("actor_id"), payload.get("actor_id"), payload.get("actor")),
        "agent_id": _first_text(explicit.get("agent_id"), payload.get("agent_id")),
        "timestamp": _first_text(
            explicit.get("timestamp"),
            payload.get("observed_at"),
            payload.get("as_of_timestamp"),
            payload.get("occurred_at"),
            payload.get("recorded_at"),
        ),
        "provenance": _clean_dict(explicit.get("provenance")) or _clean_dict(payload.get("source_attribution")),
        "hydration_refs": hydration_refs,
        "authority_class": _first_text(explicit.get("authority_class"), payload.get("authority_class"), payload.get("authority")),
        "parent_artifact": explicit.get("parent_artifact") if explicit.get("parent_artifact") not in (None, "", [], {}) else _clean_dict({
            "document_id": payload.get("document_id"),
            "document_name": payload.get("document_name"),
            "raw_source_object_id": payload.get("raw_source_object_id"),
        }),
        "local_refs": _local_refs(payload, explicit),
    }
    envelope = {k: v for k, v in envelope.items() if v not in (None, "", [], {})}
    explicit_id = _clean_str(explicit.get("envelope_id") or payload.get("source_ingest_envelope_id"))
    basis = {k: v for k, v in envelope.items() if k not in {"envelope_id", "schema"}}
    envelope["envelope_id"] = explicit_id or ("env-" + _stable_hash(basis)[:16])
    return envelope


def source_ingest_envelope_ref(envelope: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(envelope, dict) or not envelope:
        return {}
    local_refs = _clean_dict(envelope.get("local_refs"))
    out = {
        "schema": _clean_str(envelope.get("schema")) or SOURCE_INGEST_ENVELOPE_SCHEMA,
        "envelope_id": _clean_str(envelope.get("envelope_id")),
        "boundary_type": _clean_str(envelope.get("boundary_type")),
        "ingest_batch_id": _clean_str(envelope.get("ingest_batch_id")),
        "tenant_id": _clean_str(envelope.get("tenant_id")),
        "workspace_id": _clean_str(envelope.get("workspace_id")),
        "source_type": _clean_str(envelope.get("source_type")),
        "source_id": _clean_str(envelope.get("source_id")),
        "source_object_id": _clean_str(envelope.get("source_object_id")),
        "source_event_id": _clean_str(envelope.get("source_event_id")),
        "authority_class": _clean_str(envelope.get("authority_class")),
        "local_refs": local_refs,
    }
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def normalize_source_ingest_envelope_ref(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {}
    if value.get("envelope_id"):
        return source_ingest_envelope_ref(value)
    return source_ingest_envelope_ref(normalize_source_ingest_envelope(value))


def merge_source_ingest_envelope_refs(*values: Any, limit: int = 25) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        rows = value if isinstance(value, list) else [value]
        for row in rows:
            ref = normalize_source_ingest_envelope_ref(row)
            if not ref:
                continue
            key = _clean_str(ref.get("envelope_id")) or _stable_hash(ref)
            if key in seen:
                continue
            out.append(ref)
            seen.add(key)
            if len(out) >= max(1, int(limit)):
                return out
    return out


def source_ingest_batch_ids(refs: Any) -> list[str]:
    out: list[str] = []
    for ref in _as_list(refs):
        if not isinstance(ref, dict):
            continue
        value = _clean_str(ref.get("ingest_batch_id"))
        if value and value not in out:
            out.append(value)
    return out


def source_microbatch_key(refs: Any) -> str:
    batch_ids = source_ingest_batch_ids(refs)
    if batch_ids:
        return "batch:" + ",".join(sorted(batch_ids)[:3])
    envelope_ids = sorted(
        _clean_str(ref.get("envelope_id"))
        for ref in _as_list(refs)
        if isinstance(ref, dict) and _clean_str(ref.get("envelope_id"))
    )
    if envelope_ids:
        return "envelope:" + ",".join(envelope_ids[:3])
    return ""


__all__ = [
    "SOURCE_INGEST_ENVELOPE_SCHEMA",
    "merge_source_ingest_envelope_refs",
    "normalize_source_ingest_envelope",
    "normalize_source_ingest_envelope_ref",
    "source_ingest_batch_ids",
    "source_ingest_envelope_ref",
    "source_microbatch_key",
]
