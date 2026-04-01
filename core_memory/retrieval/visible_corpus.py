from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VISIBLE_STATUSES = {"open", "candidate", "promoted", "archived"}


def _semantic_text(bead: dict[str, Any]) -> str:
    title = str(bead.get("retrieval_title") or bead.get("title") or "")
    typ = str(bead.get("type") or "")
    summary = " ".join(str(x) for x in (bead.get("summary") or []))
    because = " ".join(str(x) for x in (bead.get("because") or []))
    facts = " ".join(str(x) for x in (bead.get("retrieval_facts") or []))
    tags = " ".join(str(x) for x in (bead.get("tags") or []))
    incident_id = str(bead.get("incident_id") or "")
    detail = str(bead.get("detail") or "")
    status = str(bead.get("status") or "").lower()
    detail_part = (detail[:400] if status != "archived" else "")
    return " | ".join(x for x in [title, typ, summary, because, facts, tags, incident_id, detail_part] if x).strip()


def _lexical_text(bead: dict[str, Any]) -> str:
    return " ".join(
        [
            str(bead.get("title") or ""),
            " ".join(str(x) for x in (bead.get("summary") or [])),
            " ".join(str(x) for x in (bead.get("tags") or [])),
            str(bead.get("incident_id") or ""),
        ]
    ).strip()


def _is_system_row(bead: dict[str, Any]) -> bool:
    tags = {str(x).lower() for x in (bead.get("tags") or [])}
    typ = str(bead.get("type") or "").lower()
    return ("process_flush" in tags) or (typ in {"checkpoint", "session_end", "session_start"})


def _admit(bead: dict[str, Any], include_system: bool = False) -> bool:
    status = str(bead.get("status") or "").lower()
    if status == "superseded":
        return False
    if status not in VISIBLE_STATUSES:
        return False
    if bead.get("retrieval_eligible") is False:
        return False
    if not include_system and _is_system_row(bead):
        return False
    return True


def _to_row(bead: dict[str, Any], source_surface: str) -> dict[str, Any]:
    return {
        "bead_id": str(bead.get("id") or ""),
        "status": str(bead.get("status") or ""),
        "source_surface": source_surface,
        "session_id": str(bead.get("session_id") or ""),
        "created_at": str(bead.get("created_at") or ""),
        "incident_id": str(bead.get("incident_id") or ""),
        "tags": list(bead.get("tags") or []),
        "source_turn_ids": list(bead.get("source_turn_ids") or []),
        "semantic_text": _semantic_text(bead),
        "lexical_text": _lexical_text(bead),
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
        rows[bid] = _to_row(b, "projection")

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
            rows[bid] = _to_row(merged, "session")

    out = list(rows.values())
    out.sort(key=lambda r: (str(r.get("created_at") or ""), str(r.get("bead_id") or "")), reverse=True)
    return out

