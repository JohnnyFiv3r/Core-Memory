"""Typed external memory ingest for source-attributed evidence.

This path writes semantic memory anchors, not raw source replicas. Source bodies,
document chunks, relational rows, and binary media remain in caller-owned stores
such as Ragie, Snowflake, Supabase, or another hydration backend.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.llm_client import chat_complete
from core_memory.persistence import events
from core_memory.persistence.store import MemoryStore
from core_memory.provider_config import resolve_chat_config
from core_memory.schema.normalization import (
    EXTERNAL_BEAD_TYPES,
    EXTERNAL_DOCUMENT_FLAGS as DOCUMENT_FLAGS,
    EXTERNAL_OPERATIONAL_FLAGS as OPERATIONAL_FLAGS,
    EXTERNAL_RELATIONAL_FLAGS as RELATIONAL_FLAGS,
    EXTERNAL_STATE_ASSERTION_FLAGS as STATE_ASSERTION_FLAGS,
    EXTERNAL_TRANSCRIPT_FLAGS as TRANSCRIPT_FLAGS,
    normalize_assertion_kind,
)
from core_memory.runtime.associations.coverage import on_bead_committed


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _clean_mode(value: Any, *, default: str = "off") -> str:
    mode = _clean_str(value).lower()
    return mode if mode in {"off", "auto", "llm", "model"} else default


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


def _json_object(text: str) -> dict[str, Any] | None:
    raw = _clean_str(text)
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _bounded_snapshot(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return _clean_str(value)[:400]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in sorted(value.items(), key=lambda row: str(row[0]))[:80]:
            text_key = _clean_str(key)
            if text_key:
                out[text_key] = _bounded_snapshot(item, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [_bounded_snapshot(item, depth=depth + 1) for item in value[:40]]
    if isinstance(value, tuple):
        return [_bounded_snapshot(item, depth=depth + 1) for item in list(value)[:40]]
    if isinstance(value, str):
        return value[:1800]
    return value


def _chat_config_with_model(*env_names: str):
    cfg = resolve_chat_config()
    for name in env_names:
        model = _clean_str(os.environ.get(name))
        if model:
            return replace(cfg, model=model)
    return cfg


def _section_ref_key(value: Any) -> tuple[str, ...]:
    if isinstance(value, dict):
        for field in ("section_id", "chunk_ref", "ref", "id", "label"):
            text = _clean_str(value.get(field))
            if text:
                return (field, text)
        if value:
            return tuple(sorted((str(k), str(v)) for k, v in value.items() if _clean_str(v)))
        return ()
    text = _clean_str(value)
    return ("value", text) if text else ()


def _document_scope_key(payload: dict[str, Any]) -> tuple[tuple[str, ...], ...]:
    """Stable section scope for document_reference identity.

    An empty key means the bead describes the whole document. A non-empty key
    scopes identity to one or more source sections/chunks so multiple beads can
    share the same document_id without superseding each other.
    """
    refs = [_section_ref_key(ref) for ref in _coerce_list(payload.get("section_refs"))]
    refs = [ref for ref in refs if ref]
    return tuple(sorted(refs))


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
    if flag in OPERATIONAL_FLAGS:
        return "operational_event"
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
    if bead_type == "operational_event":
        return "operational"
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
    document_scope_key = _document_scope_key(payload) if bead_type == "document_reference" else ()
    transcript_id = _clean_str(payload.get("transcript_id") or payload.get("conversation_id"))

    index = store._read_json(store.beads_dir / "index.json")
    beads = [b for b in (index.get("beads") or {}).values() if _clean_str(b.get("type")) == bead_type]

    # Pass 1 — event identity across ALL versions (including superseded), so
    # re-delivery of an already-versioned event stays idempotent.
    if source_event_id:
        for bead in beads:
            if _clean_str(bead.get("source_event_id")) == source_event_id:
                return dict(bead)

    # Operational events are state transitions: history, not mutable objects.
    # Sibling events of the same business object accumulate as the worldline
    # substrate — they never dedup or version against each other. Only the
    # event identity (pass 1) applies. Derived current state supersedes via
    # state_assertion instead.
    if bead_type == "operational_event":
        return None

    # Pass 2 — same source object, current truth only. A hit here with a new
    # source_event_id means the source was adjusted: version, don't dedup.
    for bead in beads:
        if _clean_str(bead.get("status")).lower() == "superseded":
            continue
        if bead_type == "document_reference" and source_id:
            bead_source_id = _clean_str(bead.get("source_id"))
            bead_source_record_id = _clean_str(bead.get("source_record_id"))
            bead_scope_key = _document_scope_key(bead)
            same_scope = bead_scope_key == document_scope_key
            if source_record_id:
                if bead_source_id == source_id and same_scope and bead_source_record_id == source_record_id:
                    return dict(bead)
            if document_id:
                if bead_source_id == source_id and same_scope and document_id in {
                    _clean_str(bead.get("document_id")),
                    _clean_str(bead.get("ragie_document_id")),
                }:
                    return dict(bead)
            continue
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
    if bead_type == "operational_event":
        return {
            "record_action": _clean_str(payload.get("record_action")),
            "business_object_type": _clean_str(payload.get("business_object_type")),
            "business_object_id": _clean_str(payload.get("business_object_id") or payload.get("source_record_id")),
            "source_table": _clean_str(payload.get("source_table")),
            "source_record_id": _clean_str(payload.get("source_record_id")),
            "actor": _clean_str(payload.get("actor")),
            "state_change": dict(payload.get("state_change") or {}) or None,
            "as_of_timestamp": _clean_str(payload.get("as_of_timestamp") or payload.get("occurred_at")),
            "entity_refs": _coerce_str_list(payload.get("entity_refs")),
            "attribute_tags": _coerce_str_list(payload.get("attribute_tags")),
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
            "assertion_kind": normalize_assertion_kind(payload.get("assertion_kind")),
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
        _require(payload, ("source_id", "source_event_id", "source_system"))
    if bead_type != "state_assertion" and not hydration_ref:
        raise ValueError("external_evidence: missing required fields: hydration_ref")

    if bead_type == "document_reference":
        if not _clean_str(payload.get("document_id") or payload.get("ragie_document_id")):
            raise ValueError("external_evidence: document_reference requires document_id or ragie_document_id")
        _require(payload, ("document_name",))
    elif bead_type == "operational_event":
        _require(payload, ("record_action",))
        if not _clean_str(payload.get("business_object_id") or payload.get("source_record_id")):
            raise ValueError("external_evidence: operational_event requires business_object_id or source_record_id")
        if not _clean_str(payload.get("as_of_timestamp") or payload.get("occurred_at") or payload.get("observed_at")):
            raise ValueError("external_evidence: operational_event requires as_of_timestamp, occurred_at, or observed_at")
        if not (_coerce_str_list(payload.get("entities")) or _coerce_str_list(payload.get("entity_refs"))):
            raise ValueError("external_evidence: operational_event requires entities or entity_refs")
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

    observed_at = _clean_str(payload.get("observed_at") or payload.get("as_of_timestamp") or payload.get("occurred_at"))
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


def _external_bead_judge_mode(payload: dict[str, Any]) -> str:
    explicit = _clean_mode(payload.get("_bead_judge") or payload.get("bead_judge"), default="")
    if explicit:
        return explicit
    return _clean_mode(os.getenv("CORE_MEMORY_EXTERNAL_EVIDENCE_BEAD_JUDGE_MODE"), default="off")


def _normalize_judged_external_fields(obj: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    title = _clean_str(obj.get("title"))[:200]
    summary = _coerce_summary(obj.get("summary"))
    detail = _clean_str(obj.get("detail") or obj.get("content"))[:1200]
    if title:
        out["title"] = title
    if summary:
        out["summary"] = summary
    if detail:
        out["detail"] = detail

    for field, limit in (
        ("entities", 32),
        ("topics", 32),
        ("supporting_facts", 12),
        ("because", 12),
        ("attribute_tags", 32),
    ):
        values = _coerce_str_list(obj.get(field))
        if values:
            out[field] = values[:limit]

    for field in ("state_change", "validity", "effective_from", "effective_to", "observed_at", "authority"):
        value = _clean_str(obj.get(field))
        if value:
            out[field] = value

    if isinstance(obj.get("evidence_refs"), list):
        out["evidence_refs"] = obj.get("evidence_refs")
    confidence = _coerce_float(obj.get("confidence"))
    if confidence is not None:
        out["confidence"] = min(1.0, max(0.0, confidence))
    return out


def _judge_external_bead_fields(payload: dict[str, Any], *, bead_type: str, source_kind: str) -> dict[str, Any]:
    prompt = (
        "You are Core Memory's external-evidence bead writer. Author the semantic "
        "fields for exactly one source-attributed memory bead. The caller has "
        "already limited the bead to the schema type below; do not change it. "
        "Use source payload facts only. Do not invent dates, entities, refs, or "
        "causal claims. Return JSON only with: title, summary, detail, entities, "
        "topics, supporting_facts, because, evidence_refs, state_change, validity, "
        "effective_from, effective_to, observed_at, confidence, authority.\n\n"
        f"schema_limited_type: {bead_type}\n"
        f"source_kind: {source_kind}\n"
        "source_payload:\n"
        f"{json.dumps(_bounded_snapshot(payload), ensure_ascii=False, sort_keys=True)}"
    )
    raw = chat_complete(
        prompt,
        config=_chat_config_with_model(
            "CORE_MEMORY_EXTERNAL_EVIDENCE_BEAD_FIELD_MODEL",
            "CORE_MEMORY_BEAD_FIELD_MODEL",
            "CORE_MEMORY_FRONTIER_MODEL",
        ),
        max_tokens=1400,
        temperature=0,
    )
    obj = _json_object(raw)
    if obj is None:
        raise ValueError("external_evidence: bead_judge_returned_non_object")
    normalized = _normalize_judged_external_fields(obj)
    if not normalized.get("title") or not normalized.get("summary"):
        raise ValueError("external_evidence: bead_judge_missing_title_or_summary")
    return normalized


def _apply_external_bead_judge(bead: dict[str, Any], payload: dict[str, Any], *, bead_type: str, source_kind: str) -> dict[str, Any]:
    mode = _external_bead_judge_mode(payload)
    if mode == "off":
        return bead
    try:
        judged = _judge_external_bead_fields(payload, bead_type=bead_type, source_kind=source_kind)
    except Exception:
        if mode in {"llm", "model"}:
            raise
        return bead

    out = dict(bead)
    for field, value in judged.items():
        if value not in (None, "", [], {}):
            out[field] = value
    tags = _coerce_str_list(out.get("tags"))
    for tag in ("external_evidence_bead_judge", "llm_judged"):
        if tag not in tags:
            tags.append(tag)
    out["tags"] = tags[:32]
    out["type"] = bead_type
    out["data_type_flag"] = _clean_str(bead.get("data_type_flag")) or source_kind
    return out


def _content_signature(record: dict[str, Any], bead_type: str) -> tuple:
    """Comparable content identity for idempotency vs. version decisions."""
    typed = _typed_fields(bead_type, record)
    return (
        _clean_str(record.get("title")),
        tuple(_coerce_summary(record.get("summary"))),
        _clean_str(record.get("detail") or record.get("content"))[:1200],
        tuple(sorted((str(k), str(v)) for k, v in typed.items() if v not in (None, "", [], {}))),
    )


def ingest_external_evidence(root: str, payload: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    """Write a typed external source bead and return an ingest receipt.

    Beads are immutable: when a known source object arrives with a new
    source_event_id and changed content, a new version bead is written and the
    prior version is closed via a `supersedes` chain — never edited in place.
    """
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
    predecessor_id = ""
    existing = _find_existing_external_bead(store, merged, bead_type)
    if existing:
        bead_id = _clean_str(existing.get("id"))
        incoming_event = _clean_str(merged.get("source_event_id"))
        same_event = bool(incoming_event) and _clean_str(existing.get("source_event_id")) == incoming_event
        same_content = _content_signature(existing, bead_type) == _content_signature(merged, bead_type)
        if same_event or same_content:
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
        # Same source object, new event, changed content: the source was
        # adjusted. Write the new version; close the old one below.
        predecessor_id = bead_id

    bead = _bead_payload(merged, bead_type=bead_type, source_kind=source_kind, hydration_ref=hydration_ref)
    bead = _apply_external_bead_judge(bead, merged, bead_type=bead_type, source_kind=source_kind)
    if predecessor_id:
        supersedes = _coerce_str_list(bead.get("supersedes"))
        if predecessor_id not in supersedes:
            supersedes.append(predecessor_id)
        bead["supersedes"] = supersedes
    bead_id = store.add_bead(
        type=bead.pop("type"),
        title=bead.pop("title"),
        summary=bead.pop("summary"),
        detail=bead.pop("detail", ""),
        session_id=bead.pop("session_id", None),
        source_turn_ids=bead.pop("source_turn_ids", []),
        tags=bead.pop("tags", []),
        _association_coverage=False,
        **bead,
    )
    if predecessor_id:
        store.supersede(predecessor_id, bead_id)
        store.link(
            bead_id,
            predecessor_id,
            "supersedes",
            explanation="external source adjusted: new version supersedes prior version",
            confidence=0.95,
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
    receipt = {
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
    if predecessor_id:
        receipt["status"] = "version_superseded"
        receipt["superseded_bead_id"] = predecessor_id
    coverage_trigger = "periodic_transcript_push" if bead_type == "transcript" else "typed_ingest"
    try:
        coverage = on_bead_committed(
            root=root,
            bead_id=bead_id,
            session_id=_clean_str(merged.get("session_id")) or session_id,
            trigger=coverage_trigger,
            source="external_evidence",
            run_inline=True,
        )
    except Exception as exc:  # pragma: no cover - defensive integration boundary
        coverage = {"ok": False, "error": str(exc), "association_state_by_bead": {bead_id: "failed"}}
    receipt["association_run_id"] = _clean_str(coverage.get("run_id"))
    receipt["association_trigger"] = coverage_trigger
    receipt["association_state"] = _clean_str((coverage.get("association_state_by_bead") or {}).get(bead_id)) or "failed"
    receipt["association_queued"] = False
    receipt["association_coverage"] = coverage
    return receipt


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


def ingest_operational_event(root: str, payload: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    merged = dict(payload or {})
    merged.setdefault("type", "operational_event")
    merged.setdefault("data_type_flag", "operational_event")
    return ingest_external_evidence(root, merged, session_id=session_id)


__all__ = [
    "ingest_external_evidence",
    "ingest_operational_event",
    "ingest_structured_observation",
    "ingest_document_reference",
    "ingest_state_assertion",
    "resolve_external_bead_type",
]
