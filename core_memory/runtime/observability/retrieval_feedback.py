from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core_memory.persistence.io_utils import append_jsonl, store_lock


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _parse_since(since: str | None) -> timedelta | None:
    raw = str(since or "").strip().lower()
    if not raw:
        return None
    import re

    m = re.fullmatch(r"(\d+)\s*([dh])", raw)
    if not m:
        return None
    n = int(m.group(1))
    u = m.group(2)
    if u == "d":
        return timedelta(days=n)
    if u == "h":
        return timedelta(hours=n)
    return None


def _events_path(root: str | Path) -> Path:
    return Path(root) / ".beads" / "events" / "retrieval-feedback.jsonl"


def _collect_edges(chains: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for c in chains or []:
        if not isinstance(c, dict):
            continue
        for e in (c.get("edges") or []):
            if not isinstance(e, dict):
                continue
            src = str(e.get("src") or e.get("source") or "").strip()
            dst = str(e.get("dst") or e.get("target") or "").strip()
            rel = str(e.get("rel") or e.get("relationship") or "").strip()
            if not src or not dst or not rel:
                continue
            k = (src, dst, rel)
            if k in seen:
                continue
            seen.add(k)
            out.append({"src": src, "dst": dst, "rel": rel})
    return out


def record_retrieval_feedback(
    root: str | Path,
    *,
    request: dict[str, Any],
    response: dict[str, Any],
    source: str = "memory_execute",
) -> dict[str, Any]:
    req = dict(request or {})
    out = dict(response or {})

    results = [dict(r or {}) for r in (out.get("results") or [])]
    top = results[0] if results else {}
    answer_outcome = str(out.get("answer_outcome") or "")

    success = bool(out.get("ok")) and bool(results) and answer_outcome != "abstain"

    row = {
        "id": f"rf-{uuid.uuid4().hex[:12]}",
        "created_at": _now(),
        "source": str(source or "memory_execute"),
        "request": {
            "query": str(req.get("raw_query") or req.get("query_text") or req.get("query") or ""),
            "intent": str(req.get("intent") or "remember"),
            "as_of": str(req.get("as_of") or "") or None,
            "k": int(req.get("k") or 10),
            "grounding_mode": str(req.get("grounding_mode") or "search_only"),
        },
        "response": {
            "ok": bool(out.get("ok")),
            "answer_outcome": answer_outcome,
            "answer_reason": str(((out.get("answer_policy") or {}).get("decision_reason") or "")),
            "retrieval_mode": str(out.get("retrieval_mode") or ""),
            "result_count": int(len(results)),
            "top": {
                "bead_id": str(top.get("bead_id") or ""),
                "score": float(top.get("score") or 0.0),
                "source_surface": str(top.get("source_surface") or ""),
                "anchor_reason": str(top.get("anchor_reason") or ""),
                "claim_slot_key": str(top.get("claim_slot_key") or ""),
                "claim_id": str(top.get("claim_id") or ""),
            },
            "result_bead_ids": [str(r.get("bead_id") or "") for r in results[:10] if str(r.get("bead_id") or "")],
            "claim_slots": sorted(
                {
                    str(r.get("claim_slot_key") or "")
                    for r in results
                    if str(r.get("claim_slot_key") or "")
                }
            ),
            "edges": _collect_edges(list(out.get("chains") or [])),
            "warnings": list(out.get("warnings") or []),
            "retrieval_stages": dict(out.get("retrieval_stages") or {}),
            "entity_context": {
                "resolved_entity_ids": list(((out.get("entity_context") or {}).get("resolved_entity_ids") or [])),
                "matched_aliases": list(((out.get("entity_context") or {}).get("matched_aliases") or [])),
            },
        },
        "success": bool(success),
    }

    path = _events_path(root)
    with store_lock(Path(root)):
        append_jsonl(path, row)

    return {"ok": True, "event_id": row["id"], "path": str(path), "success": bool(success)}


def read_retrieval_feedback(root: str | Path, *, since: str = "30d", limit: int = 500) -> list[dict[str, Any]]:
    p = _events_path(root)
    if not p.exists():
        return []

    cutoff = None
    delta = _parse_since(since)
    if delta is not None:
        cutoff = datetime.now(timezone.utc) - delta

    rows: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            if cutoff is not None:
                dt = _parse_iso(str(row.get("created_at") or ""))
                if dt is not None and dt < cutoff:
                    continue
            rows.append(row)

    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return rows[: max(1, int(limit))]


def summarize_retrieval_feedback(root: str | Path, *, since: str = "30d", limit: int = 500) -> dict[str, Any]:
    rows = read_retrieval_feedback(root, since=since, limit=limit)
    success_rows = [r for r in rows if bool(r.get("success"))]

    bead_hits = Counter()
    edge_hits = Counter()
    slot_hits = Counter()
    anchor_reason_hits = Counter()

    for r in success_rows:
        resp = dict(r.get("response") or {})
        for bid in (resp.get("result_bead_ids") or []):
            b = str(bid or "")
            if b:
                bead_hits[b] += 1
        for e in (resp.get("edges") or []):
            if not isinstance(e, dict):
                continue
            src = str(e.get("src") or "")
            dst = str(e.get("dst") or "")
            rel = str(e.get("rel") or "")
            if src and dst and rel:
                edge_hits[(src, dst, rel)] += 1
        for s in (resp.get("claim_slots") or []):
            ss = str(s or "")
            if ss:
                slot_hits[ss] += 1
        ar = str((resp.get("top") or {}).get("anchor_reason") or "")
        if ar:
            anchor_reason_hits[ar] += 1

    top_beads = [{"bead_id": k, "hits": int(v)} for k, v in bead_hits.most_common(50)]
    top_edges = [{"src": k[0], "dst": k[1], "rel": k[2], "hits": int(v)} for k, v in edge_hits.most_common(50)]
    top_slots = [{"slot_key": k, "hits": int(v)} for k, v in slot_hits.most_common(50)]

    return {
        "schema": "core_memory.retrieval_feedback_summary.v1",
        "since": since,
        "counts": {
            "events": len(rows),
            "successful": len(success_rows),
        },
        "top_beads": top_beads,
        "top_edges": top_edges,
        "top_slots": top_slots,
        "anchor_reason_histogram": dict(anchor_reason_hits),
    }
