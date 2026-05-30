"""Ragie retrieve adapter for multi-store recall fan-out (#15).

Uses urllib.request (stdlib) — no httpx dependency.
POST /retrievals with bearer auth. Field names are exact per the Ragie OpenAPI spec.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any

from core_memory.retrieval.contracts import EvidenceItem

_RAGIE_API_BASE = "https://api.ragie.ai"


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
    api_key: str,
    top_k: int = 8,
    rerank: bool = True,
    partition: str | None = None,
    filter: dict[str, Any] | None = None,
) -> list[EvidenceItem]:
    """POST /retrievals with bearer auth. Returns empty list on any exception."""
    body: dict[str, Any] = {"query": query, "top_k": top_k, "rerank": rerank}
    if partition is not None:
        body["partition"] = partition
    if filter is not None:
        body["filter"] = filter

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{_RAGIE_API_BASE}/retrievals",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []

    chunks = payload.get("scored_chunks") if isinstance(payload, dict) else None
    if not isinstance(chunks, list):
        return []

    items: list[EvidenceItem] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        chunk_id = str(chunk.get("id") or "").strip()
        if not chunk_id:
            continue
        doc_meta = chunk.get("document_metadata") if isinstance(chunk.get("document_metadata"), dict) else {}
        unifying_id = str(doc_meta.get("core_memory_unifying_id") or "").strip() or None
        raw_score = chunk.get("score")
        try:
            score: float | None = float(raw_score)
        except (TypeError, ValueError):
            score = None
        items.append(EvidenceItem(
            bead_id="",
            type="document_chunk",
            title=str(chunk.get("document_name") or "").strip(),
            content_excerpt=str(chunk.get("text") or "")[:600],
            score=score,
            source_store="ragie",
            source_ref=chunk_id,
            unifying_id=unifying_id,
            metadata={
                "document_id": str(chunk.get("document_id") or "").strip(),
                "document_name": str(chunk.get("document_name") or "").strip(),
                "source_links": chunk.get("links") or {},
                "chunk_index": chunk.get("index"),
            },
        ))

    return _normalize_scores(items)
