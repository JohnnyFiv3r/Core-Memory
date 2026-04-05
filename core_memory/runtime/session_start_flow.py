from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..persistence.store import MemoryStore
from ..write_pipeline.continuity_injection import load_continuity_injection


def find_existing_session_start_bead(root: str, session_id: str) -> dict[str, Any] | None:
    store = MemoryStore(root=root)
    try:
        idx = store._read_json(Path(root) / ".beads" / "index.json")
        beads = idx.get("beads") or {}
        matches: list[dict[str, Any]] = []
        for bead in beads.values():
            if str((bead or {}).get("session_id") or "") != str(session_id):
                continue
            if str((bead or {}).get("type") or "") != "session_start":
                continue
            matches.append(dict(bead))
        if not matches:
            return None
        matches.sort(key=lambda b: str(b.get("created_at") or ""), reverse=True)
        return matches[0]
    finally:
        store.close()


def build_session_start_snapshot(*, session_id: str, continuity: dict[str, Any], max_items: int) -> dict[str, Any]:
    raw_records = list(continuity.get("records") or [])

    def _is_session_start_record(rec: dict[str, Any]) -> bool:
        typ = str((rec or {}).get("type") or "").strip().lower()
        if typ == "session_start":
            return True
        tags = {str(t).strip().lower() for t in ((rec or {}).get("tags") or []) if str(t).strip()}
        return "session_start" in tags

    filtered_records = [r for r in raw_records if not _is_session_start_record(r)]
    filtered_session_start_count = max(0, len(raw_records) - len(filtered_records))
    records = filtered_records[: max(1, int(max_items))]
    authority = str(continuity.get("authority") or "unknown")
    included_bead_ids = [str(x) for x in (continuity.get("included_bead_ids") or []) if str(x).strip()]
    meta = dict(continuity.get("meta") or {})

    source_turn_ids: list[str] = []
    for rec in records:
        for tid in (rec.get("source_turn_ids") or []):
            stid = str(tid or "").strip()
            if stid and stid not in source_turn_ids:
                source_turn_ids.append(stid)
    if not source_turn_ids:
        source_turn_ids = [f"session-start:{session_id}"]

    summary = [
        f"Continuity authority: {authority}",
        f"Carried records: {len(records)}",
        f"Included bead refs: {len(included_bead_ids)}",
    ]
    if filtered_session_start_count:
        summary.append(f"Filtered prior session_start records: {filtered_session_start_count}")
    for rec in records[:3]:
        r_type = str(rec.get("type") or "memory")
        r_title = str(rec.get("title") or "").strip() or r_type
        summary.append(f"{r_type}: {r_title}")

    detail_lines = [
        "Session-start continuity snapshot.",
        f"session_id={session_id}",
        f"authority={authority}",
        f"record_count={len(records)}",
        f"included_bead_ids={','.join(included_bead_ids) if included_bead_ids else '-'}",
    ]
    if filtered_session_start_count:
        detail_lines.append(f"filtered_prior_session_start_records={filtered_session_start_count}")
    if meta:
        detail_lines.append(f"meta={json.dumps(meta, ensure_ascii=False, sort_keys=True)}")
    for i, rec in enumerate(records, 1):
        r_type = str(rec.get("type") or "memory")
        r_title = str(rec.get("title") or "")
        r_summary = rec.get("summary")
        if isinstance(r_summary, list):
            r_summary = " ".join(str(x) for x in r_summary)
        r_summary = str(r_summary or rec.get("detail") or "").strip()
        detail_lines.append(f"{i}. [{r_type}] {r_title}: {r_summary}")

    return {
        "type": "session_start",
        "title": "Session start",
        "summary": summary[:8],
        "detail": "\n".join(detail_lines),
        "source_turn_ids": source_turn_ids,
        "tags": ["session_start", "lifecycle_boundary", "continuity_snapshot"],
        "retrieval_eligible": True,
        "continuity_authority": authority,
        "continuity_record_count": len(records),
        "continuity_filtered_session_start_count": filtered_session_start_count,
        "continuity_included_bead_ids": included_bead_ids,
    }


def process_session_start_impl(
    *,
    root: str,
    session_id: str,
    source: str = "runtime",
    max_items: int = 80,
) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "missing_session_id"}

    existing = find_existing_session_start_bead(root=root, session_id=sid)
    if existing:
        return {
            "ok": True,
            "created": False,
            "bead_id": str(existing.get("id") or ""),
            "session_id": sid,
            "source": source,
            "type": "session_start",
        }

    continuity = load_continuity_injection(workspace_root=root, max_items=max_items)
    bead = build_session_start_snapshot(session_id=sid, continuity=continuity, max_items=max_items)

    store = MemoryStore(root=root)
    try:
        bead_id = store.add_bead(
            type="session_start",
            title=str(bead.get("title") or "Session start"),
            summary=list(bead.get("summary") or []),
            detail=str(bead.get("detail") or ""),
            session_id=sid,
            source_turn_ids=list(bead.get("source_turn_ids") or []),
            tags=list(bead.get("tags") or []),
            retrieval_eligible=bool(bead.get("retrieval_eligible", True)),
            continuity_authority=str(bead.get("continuity_authority") or ""),
            continuity_record_count=int(bead.get("continuity_record_count") or 0),
            continuity_filtered_session_start_count=int(bead.get("continuity_filtered_session_start_count") or 0),
            continuity_included_bead_ids=list(bead.get("continuity_included_bead_ids") or []),
            created_by_source=str(source or "runtime"),
        )
    finally:
        store.close()

    return {
        "ok": True,
        "created": True,
        "bead_id": bead_id,
        "session_id": sid,
        "source": source,
        "type": "session_start",
        "authority": continuity.get("authority"),
        "record_count": int(bead.get("continuity_record_count") or 0),
    }
