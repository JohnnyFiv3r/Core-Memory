from __future__ import annotations


def normalize_bead_for_cli(bead: dict) -> dict:
    b = dict(bead or {})
    title = str(b.get("title") or "Untitled")[:200]
    summary_items = b.get("summary") or []
    if isinstance(summary_items, str):
        summary_items = [summary_items]
    summary = [str(x)[:300] for x in summary_items if str(x).strip()]
    if not summary:
        summary = [title]

    out = {
        "type": str(b.get("type") or "context"),
        "title": title,
        "summary": summary,
        "scope": str(b.get("scope") or "project"),
        "authority": str(b.get("authority") or "agent"),
    }

    confidence = b.get("confidence")
    if confidence is not None:
        try:
            out["confidence"] = float(confidence)
        except Exception:
            pass

    tags = b.get("tags")
    if isinstance(tags, list):
        out["tags"] = [str(x) for x in tags if str(x).strip()]

    return out
