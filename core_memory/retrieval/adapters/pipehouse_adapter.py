"""PipeHouse read adapter for multi-store recall fan-out (#15).

Uses urllib.request (stdlib). URL configured via CORE_MEMORY_PIPEHOUSE_URL.
Raises on HTTP/network errors so fanout_recall marks the store unavailable.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
import urllib.parse
from typing import Any

from core_memory.retrieval.contracts import EvidenceItem


def _normalize_scores(items: list[EvidenceItem]) -> list[EvidenceItem]:
    scores = [i.score for i in items if i.score is not None]
    if not scores:
        return items
    lo, hi = min(scores), max(scores)
    if hi == lo:
        return items
    for item in items:
        if item.score is not None:
            item.score = (item.score - lo) / (hi - lo)
    return items


def retrieve(
    query: str,
    *,
    base_url: str,
    top_k: int = 8,
    filters: dict[str, Any] | None = None,
) -> list[EvidenceItem]:
    """Call PipeHouse read endpoint. Raises on HTTP/network errors."""
    body: dict[str, Any] = {"query": query, "top_k": top_k}
    if filters is not None:
        body["filters"] = filters

    url = base_url.rstrip("/") + "/retrieve"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    records = None
    if isinstance(payload, dict):
        records = payload.get("records") or payload.get("results") or payload.get("data")
    elif isinstance(payload, list):
        records = payload
    if not isinstance(records, list):
        return []

    items: list[EvidenceItem] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        record_id = str(record.get("record_id") or record.get("id") or "").strip()
        if not record_id:
            continue
        raw_score = record.get("relevance_score") if record.get("relevance_score") is not None else record.get("score")
        try:
            score: float | None = float(raw_score)
        except (TypeError, ValueError):
            score = None
        items.append(EvidenceItem(
            bead_id="",
            type="data_insight",
            title=str(record.get("title") or "").strip(),
            content_excerpt=str(record.get("content") or "")[:600],
            score=score,
            source_store="pipehouse",
            source_ref=record_id,
            unifying_id=None,
            metadata={
                "entity_refs": record.get("entity_refs") or [],
                "attribute_tags": record.get("attribute_tags") or [],
                "as_of_timestamp": str(record.get("as_of_timestamp") or "").strip(),
                # Expose as created_at so _filter_evidence_by_as_of works correctly.
                "created_at": str(record.get("as_of_timestamp") or "").strip() or None,
                "source_table": str(record.get("source_table") or "").strip(),
            },
        ))

    return _normalize_scores(items)
