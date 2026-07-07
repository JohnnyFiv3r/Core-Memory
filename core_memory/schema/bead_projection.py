"""Canonical retrieval-text projection for a bead dict.

Single source of truth for the text sent to the vector index and used for
lexical scoring.  All embedding and indexing paths must use build_retrieval_text()
so that post-write embeddings and read-path index rebuilds are identical.
"""
from __future__ import annotations

from typing import Any


_LIST_FIELDS = (
    # summary / rationale tier
    "summary",
    "because",
    "retrieval_facts",
    "supporting_facts",
    "source_refs",
    "entity_refs",
    "attribute_tags",
    "message_refs",
    "speaker_refs",
    "derived_from",
    "derived_from_bead_ids",
    # association anchor tier — the main gap vs. the old projection
    "entities",
    "entity_ids",
    "topics",
    "decision_keys",
    "goal_keys",
    "action_keys",
    "outcome_keys",
    "time_keys",
    "evidence_refs",
    "cause_candidates",
    "effect_candidates",
    # existing context
    "tags",
)

_SCALAR_FIELDS = (
    "data_type_flag",
    "source_id",
    "source_event_id",
    "source_system",
    "source_kind",
    "source_ref",
    "core_memory_unifying_id",
    "transcript_id",
    "conversation_id",
    "source_thread_id",
    "source_session_id",
    "document_id",
    "raw_source_object_id",
    "ragie_document_id",
    "document_name",
    "mime_type",
    "document_kind",
    "document_date",
    "author_or_owner",
    "source_table",
    "source_record_id",
    "record_action",
    "record_grain",
    "business_object_type",
    "business_object_id",
    "actor",
    "metric_name",
    "metric_unit",
    "currency",
    "as_of_timestamp",
    "assertion_kind",
    "assertion_subject",
    "assertion_predicate",
    "assertion_value",
)


def build_retrieval_text(bead: dict[str, Any]) -> str:
    """Return the canonical embedding/retrieval text for a bead.

    Covers identity, rationale, and association-anchor tiers so the vector
    index reflects the bead's full semantic content, not just its readable
    summary.
    """
    parts: list[str] = []

    # Identity
    title = str(bead.get("retrieval_title") or bead.get("title") or "")
    if title:
        parts.append(title)
    btype = str(bead.get("type") or "")
    if btype:
        parts.append(btype)

    for field in _SCALAR_FIELDS:
        value = str(bead.get(field) or "").strip()
        if value:
            parts.append(value)
            spaced = value.replace("_", " ")
            if spaced != value:
                parts.append(spaced)

    # All list-valued fields; add underscore→space variant so queries like
    # "approve budget" match stored keys like "approve_budget".
    for field in _LIST_FIELDS:
        items = bead.get(field) or []
        if isinstance(items, list):
            raw = " ".join(str(v) for v in items if v)
            if raw:
                parts.append(raw)
                spaced = raw.replace("_", " ")
                if spaced != raw:
                    parts.append(spaced)

    # incident_id: sparse but high-signal for exact recall
    incident = str(bead.get("incident_id") or "")
    if incident:
        parts.append(incident)

    # detail: non-archived only, capped to keep embedding vectors stable
    status = str(bead.get("status") or "").lower()
    detail = str(bead.get("detail") or "")
    if detail and status != "archived":
        parts.append(detail[:400])

    # Claims: slot/kind/reason terms are often the actual query words
    for claim in (bead.get("claims") or []):
        if not isinstance(claim, dict):
            continue
        subject = str(claim.get("subject") or "")
        slot = str(claim.get("slot") or "")
        kind = str(claim.get("claim_kind") or "")
        value = str(claim.get("value") or "")
        reason = str(claim.get("reason_text") or "")
        if subject:
            parts.append(subject)
        if slot:
            parts.append(slot.replace("_", " "))
            parts.append(slot)
        if kind:
            parts.append(kind.replace("_", " "))
        if value and len(value) < 200:
            parts.append(value)
        if reason and len(reason) < 240:
            parts.append(reason)

    return " | ".join(p for p in parts if p).strip()
