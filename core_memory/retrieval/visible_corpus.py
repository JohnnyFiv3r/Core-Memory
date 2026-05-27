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
    text_parts = [title, typ, summary, because, facts, tags, incident_id, detail_part]
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
    # Each line is treated as a delta update to the bead's state. Admission is
    # checked on the MERGED state so tombstone lines (e.g. status="retracted") are
    # applied correctly: a retraction line removes a previously-visible bead.
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
            merged = dict((rows.get(bid) or {}).get("bead") or {})
            merged.update(bead)
            merged.setdefault("id", bid)
            if not _admit(merged, include_system=include_system):
                rows.pop(bid, None)
                continue
            rows[bid] = _to_row(merged, "session")

    out = list(rows.values())
    out.sort(key=lambda r: (str(r.get("created_at") or ""), str(r.get("bead_id") or "")), reverse=True)
    return out
