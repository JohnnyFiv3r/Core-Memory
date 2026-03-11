"""
Context-aware retrieval with strict->fallback matching + deep recall.

Moved from store.py per Codex Phase 2 refactor.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .query_norm import _tokenize, _expand_query_tokens, _is_memory_intent


def retrieve_with_context(
    index: dict,
    bead_uncompact_fn: Optional[callable] = None,
    *,
    query_text: str = "",
    context_tags: Optional[list[str]] = None,
    limit: int = 20,
    strict_first: bool = True,
    deep_recall: bool = False,
    max_uncompact_per_turn: int = 2,
    auto_memory_intent: bool = True,
) -> dict:
    """Context-aware retrieval with strict->fallback matching + bounded deep recall.

    Behavior:
    - strict pass: require overlap with requested context_tags
    - fallback pass: fill remaining slots by recency if strict underflows
    - deep recall (optional/heuristic): uncompact top compacted/archived hits when memory-intent detected
    
    Args:
        index: The bead index dict (read from store)
        bead_uncompact_fn: Optional function to uncompact a bead (signature: bead_id -> dict)
        query_text: Query string
        context_tags: Required context tags for strict matching
        limit: Max results
        strict_first: Use strict then fallback approach
        deep_recall: Enable deep recall (uncompaction)
        max_uncompact_per_turn: Max beads to uncompact per turn
        auto_memory_intent: Auto-detect memory intent for deep recall
    """
    beads = list(index.get("beads", {}).values())
    beads = [b for b in beads if str(b.get("status", "")).lower() != "superseded"]

    req_tags = [str(t).strip().lower() for t in (context_tags or []) if str(t).strip()]
    req_set = set(req_tags)
    query_tokens = _expand_query_tokens(query_text, _tokenize(query_text), max_extra=24)

    def score(bead: dict) -> tuple:
        bead_tags = set([str(t).strip().lower() for t in (bead.get("context_tags") or []) if str(t).strip()])
        tag_overlap = len(req_set.intersection(bead_tags)) if req_set else 0
        text_tokens = _tokenize((bead.get("title") or "") + " " + " ".join(bead.get("summary") or []))
        text_overlap = len(query_tokens.intersection(text_tokens)) if query_tokens else 0
        ts = bead.get("promoted_at") or bead.get("created_at") or ""
        return (tag_overlap, text_overlap, ts)

    ranked = sorted(beads, key=score, reverse=True)

    strict = []
    fallback = []
    for b in ranked:
        bead_tags = set([str(t).strip().lower() for t in (b.get("context_tags") or []) if str(t).strip()])
        tag_overlap = len(req_set.intersection(bead_tags)) if req_set else 0
        row = {
            "id": b.get("id"),
            "type": b.get("type"),
            "title": b.get("title"),
            "summary": (b.get("summary") or [])[:2],
            "status": b.get("status"),
            "context_tags": b.get("context_tags") or [],
            "tag_overlap": tag_overlap,
            "created_at": b.get("created_at"),
            "detail_present": bool((b.get("detail") or "").strip()),
        }
        if req_set and tag_overlap > 0:
            strict.append(row)
        else:
            fallback.append(row)

    selected = []
    mode = "strict"
    if strict_first and req_set:
        selected.extend(strict[:limit])
        if len(selected) < limit:
            mode = "strict+fallback"
            selected.extend(fallback[: max(0, limit - len(selected))])
    else:
        mode = "fallback" if req_set else "global"
        selected = (strict + fallback)[:limit]

    should_deep_recall = bool(deep_recall or (auto_memory_intent and _is_memory_intent(query_text)))
    uncompact_budget = max(0, int(max_uncompact_per_turn))
    uncompact_attempted = []
    uncompact_applied = []

    if should_deep_recall and uncompact_budget > 0 and bead_uncompact_fn:
        candidates = []
        for row in selected:
            status = str(row.get("status") or "").lower()
            if status in {"archived", "compacted"} and not row.get("detail_present"):
                candidates.append(row)

        for row in candidates[:uncompact_budget]:
            bid = str(row.get("id") or "")
            if not bid:
                continue
            uncompact_attempted.append(bid)
            res = bead_uncompact_fn(bid)
            if res.get("ok"):
                uncompact_applied.append(bid)

        if uncompact_applied:
            # Refresh selected rows to expose newly-restored detail snippets.
            # (Caller would need to re-query to get fresh data)
            refreshed = []
            for row in selected:
                if row["id"] in uncompact_applied:
                    # Mark as potentially refreshed
                    row = dict(row)
                    row["detail_present"] = True
                    row["detail_preview"] = "[uncompacted]"
                refreshed.append(row)
            selected = refreshed

    return {
        "ok": True,
        "mode": mode,
        "requested_context_tags": req_tags,
        "query_token_count": len(query_tokens),
        "strict_count": len(strict),
        "fallback_count": len(fallback),
        "deep_recall": {
            "enabled": should_deep_recall,
            "auto_memory_intent": bool(auto_memory_intent),
            "query_memory_intent": bool(_is_memory_intent(query_text)),
            "max_uncompact_per_turn": uncompact_budget,
            "attempted": uncompact_attempted,
            "applied": uncompact_applied,
        },
        "results": selected[:limit],
    }
