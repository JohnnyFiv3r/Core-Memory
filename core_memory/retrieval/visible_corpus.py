from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core_memory.runtime.turn_archive import find_turn_record

VISIBLE_STATUSES = {"open", "candidate", "promoted", "archived"}


def _turn_record_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    ts = str(row.get("ts") or "").strip()
    if ts:
        parts.append(ts)
    metadata = dict(row.get("metadata") or {})
    for key in ("locomo_session_date_time", "session_date_time", "blip_caption", "query"):
        value = str(metadata.get(key) or "").strip()
        if value:
            parts.append(value)
    for turn in row.get("turns") or []:
        if not isinstance(turn, dict):
            continue
        speaker = str(turn.get("speaker") or "").strip()
        content = str(turn.get("content") or "").strip()
        turn_metadata = dict(turn.get("metadata") or {})
        for key in ("locomo_session_date_time", "session_date_time", "blip_caption", "query"):
            value = str(turn_metadata.get(key) or "").strip()
            if value:
                parts.append(value)
        if speaker and content:
            parts.append(f"{speaker}: {content}")
        elif content:
            parts.append(content)
    turn_text = str(row.get("turn_text") or "").strip()
    if turn_text:
        parts.append(turn_text)
    return " | ".join(x for x in parts if x).strip()


def _transcript_text(root: Path, bead: dict[str, Any]) -> str:
    parts: list[str] = []
    session_id = str(bead.get("session_id") or "").strip() or None
    for turn_id in list(bead.get("source_turn_ids") or [])[:4]:
        tid = str(turn_id or "").strip()
        if not tid:
            continue
        row = find_turn_record(root=root, turn_id=tid, session_id=session_id)
        if row is None and session_id is not None:
            row = find_turn_record(root=root, turn_id=tid)
        if row:
            text = _turn_record_text(row)
            if text:
                parts.append(text)
    return " | ".join(parts).strip()


def _semantic_text(bead: dict[str, Any]) -> str:
    title = str(bead.get("retrieval_title") or bead.get("title") or "")
    typ = str(bead.get("type") or "")
    summary = " ".join(str(x) for x in (bead.get("summary") or []))
    because = " ".join(str(x) for x in (bead.get("because") or []))
    facts = " ".join(str(x) for x in (bead.get("retrieval_facts") or []))
    tags = " ".join(str(x) for x in (bead.get("tags") or []))
    incident_id = str(bead.get("incident_id") or "")
    transcript_text = str(bead.get("_retrieval_transcript_text") or "")
    detail = str(bead.get("detail") or "")
    status = str(bead.get("status") or "").lower()
    detail_part = (detail[:400] if status != "archived" else "")
    text_parts = [title, typ, summary, because, facts, tags, incident_id, transcript_text, detail_part]
    # Append claim fields for semantic indexing.  Slot/kind/reason terms are
    # often the actual query words (e.g. "timezone", "response format", "why"),
    # while the value alone can be opaque ("America/Chicago", "bullet_lists").
    if bead.get("claims"):
        for claim in bead["claims"]:
            subject = claim.get("subject", "")
            slot = claim.get("slot", "")
            claim_kind = claim.get("claim_kind", "")
            value = claim.get("value", "")
            reason = claim.get("reason_text", "")
            if subject:
                text_parts.append(subject)
            if slot:
                text_parts.append(str(slot).replace("_", " "))
                text_parts.append(str(slot))
            if claim_kind:
                text_parts.append(str(claim_kind).replace("_", " "))
            if value and len(value) < 200:
                text_parts.append(value)
            if reason and len(reason) < 240:
                text_parts.append(reason)
    return " | ".join(x for x in text_parts if x).strip()


def _lexical_text(bead: dict[str, Any]) -> str:
    claim_parts: list[str] = []
    for claim in bead.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        for key in ("subject", "slot", "claim_kind", "value"):
            value = str(claim.get(key) or "").strip()
            if value:
                claim_parts.append(value)
                claim_parts.append(value.replace("_", " "))
    return " ".join(
        [
            str(bead.get("title") or ""),
            " ".join(str(x) for x in (bead.get("summary") or [])),
            " ".join(str(x) for x in (bead.get("tags") or [])),
            str(bead.get("incident_id") or ""),
            str(bead.get("_retrieval_transcript_text") or ""),
            " ".join(claim_parts),
        ]
    ).strip()


def _is_system_row(bead: dict[str, Any]) -> bool:
    tags = {str(x).lower() for x in (bead.get("tags") or [])}
    typ = str(bead.get("type") or "").lower()
    # session_start is intentionally searchable as a first-class continuity
    # snapshot. Keep operational rows (flush/checkpoint/end) out by default.
    return ("process_flush" in tags) or (typ in {"checkpoint", "session_end"})


def _admit(bead: dict[str, Any], include_system: bool = False) -> bool:
    status = str(bead.get("status") or "").lower()
    if status == "superseded":
        return False
    if status not in VISIBLE_STATUSES:
        return False
    # Canonical retrieval keeps visible statuses searchable immediately.
    # retrieval_eligible remains advisory for future stricter gating phases.
    if not include_system and _is_system_row(bead):
        return False
    return True


def _to_row(bead: dict[str, Any], source_surface: str, *, root: Path | None = None) -> dict[str, Any]:
    b = dict(bead)
    if root is not None:
        transcript_text = _transcript_text(root, b)
        if transcript_text:
            b["_retrieval_transcript_text"] = transcript_text
    return {
        "bead_id": str(bead.get("id") or ""),
        "status": str(bead.get("status") or ""),
        "source_surface": source_surface,
        "session_id": str(bead.get("session_id") or ""),
        "created_at": str(bead.get("created_at") or ""),
        "incident_id": str(bead.get("incident_id") or ""),
        "tags": list(bead.get("tags") or []),
        "source_turn_ids": list(b.get("source_turn_ids") or []),
        "semantic_text": _semantic_text(b),
        "lexical_text": _lexical_text(b),
        "bead": bead,
    }


def build_visible_corpus(root: str | Path, *, include_system: bool = False) -> list[dict[str, Any]]:
    root_p = Path(root)
    idx_file = root_p / ".beads" / "index.json"
    if not idx_file.exists():
        return []
    idx = json.loads(idx_file.read_text(encoding="utf-8"))
    projection = {str(k): v for k, v in (idx.get("beads") or {}).items() if isinstance(v, dict)}

    rows: dict[str, dict[str, Any]] = {}
    for bid, bead in projection.items():
        if not _admit(bead, include_system=include_system):
            continue
        b = dict(bead)
        b.setdefault("id", bid)
        rows[bid] = _to_row(b, "projection", root=root_p)

    # session surface overlays mutable fields
    session_dir = root_p / ".beads"
    for p in session_dir.glob("session-*.jsonl"):
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                bead = json.loads(ln)
            except Exception:
                continue
            if not isinstance(bead, dict):
                continue
            bid = str(bead.get("id") or "")
            if not bid:
                continue
            if not _admit(bead, include_system=include_system):
                continue
            merged = dict((rows.get(bid) or {}).get("bead") or {})
            merged.update(bead)
            rows[bid] = _to_row(merged, "session", root=root_p)

    out = list(rows.values())
    out.sort(key=lambda r: (str(r.get("created_at") or ""), str(r.get("bead_id") or "")), reverse=True)
    return out
