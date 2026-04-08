from __future__ import annotations

from typing import Any, Optional


def retrieve_with_context_for_store(
    store: Any,
    *,
    query_text: str = "",
    context_tags: Optional[list[str]] = None,
    limit: int = 20,
    strict_first: bool = True,
    deep_recall: bool = False,
    max_uncompact_per_turn: int = 2,
    auto_memory_intent: bool = True,
) -> dict:
    """Context-aware retrieval with strict->fallback matching + bounded deep recall."""
    index = store._read_json(store.beads_dir / "index.json")
    beads = list(index.get("beads", {}).values())
    beads = [b for b in beads if str(b.get("status", "")).lower() != "superseded"]

    req_tags = [str(t).strip().lower() for t in (context_tags or []) if str(t).strip()]
    req_set = set(req_tags)
    query_tokens = store._expand_query_tokens(query_text, store._tokenize(query_text), max_extra=24)

    def score(bead: dict) -> tuple:
        bead_tags = {str(t).strip().lower() for t in (bead.get("context_tags") or []) if str(t).strip()}
        tag_overlap = len(req_set.intersection(bead_tags)) if req_set else 0
        text_tokens = store._tokenize((bead.get("title") or "") + " " + " ".join(bead.get("summary") or []))
        text_overlap = len(query_tokens.intersection(text_tokens)) if query_tokens else 0
        ts = bead.get("promoted_at") or bead.get("created_at") or ""
        return (tag_overlap, text_overlap, ts)

    ranked = sorted(beads, key=score, reverse=True)

    strict = []
    fallback = []
    for b in ranked:
        bead_tags = {str(t).strip().lower() for t in (b.get("context_tags") or []) if str(t).strip()}
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

    should_deep_recall = bool(deep_recall or (auto_memory_intent and store._is_memory_intent(query_text)))
    uncompact_budget = max(0, int(max_uncompact_per_turn))
    uncompact_attempted = []
    uncompact_applied = []

    if should_deep_recall and uncompact_budget > 0:
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
            res = store.uncompact(bid)
            if res.get("ok"):
                uncompact_applied.append(bid)

        if uncompact_applied:
            idx2 = store._read_json(store.beads_dir / "index.json")
            bead_map = idx2.get("beads", {})
            refreshed = []
            for row in selected:
                bead = bead_map.get(str(row.get("id") or ""), {})
                detail = (bead.get("detail") or "").strip()
                row2 = dict(row)
                row2["detail_present"] = bool(detail)
                if detail:
                    row2["detail_preview"] = detail[:240]
                refreshed.append(row2)
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
            "query_memory_intent": bool(store._is_memory_intent(query_text)),
            "max_uncompact_per_turn": uncompact_budget,
            "attempted": uncompact_attempted,
            "applied": uncompact_applied,
        },
        "results": selected[:limit],
    }


__all__ = ["retrieve_with_context_for_store"]
