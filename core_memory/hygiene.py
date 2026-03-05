from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .io_utils import append_jsonl, atomic_write_json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_title(title: str) -> str:
    t = (title or "").strip()
    t = re.sub(r"^\s*\[\[\s*reply_to_current\s*\]\]\s*", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:120]


def _infer_type(cur_type: str, text: str) -> str | None:
    t = (text or "").lower()
    if cur_type != "context":
        return None
    if re.search(r"\b(decision|decided|policy reset|rationale|candidate-only|agent-authoritative)\b", t):
        return "decision"
    if re.search(r"\b(evidence|metrics|kpi|measured|observed|inflation|score)\b", t):
        return "evidence"
    if re.search(r"\b(outcome|result|completed|after|now)\b", t):
        return "outcome"
    if re.search(r"\b(lesson|learned|takeaway)\b", t):
        return "lesson"
    return None


def curated_type_title_hygiene(root: Path, bead_ids: list[str], *, apply: bool = False) -> dict:
    idx_file = root / ".beads" / "index.json"
    if not idx_file.exists():
        return {"ok": False, "error": "index_missing"}
    idx = json.loads(idx_file.read_text(encoding="utf-8"))
    beads = idx.get("beads") or {}

    changes = []
    for bid in sorted(set([str(x) for x in bead_ids if str(x)])):
        b = beads.get(bid)
        if not b:
            continue
        old_title = str(b.get("title") or "")
        new_title = _clean_title(old_title)
        if not new_title:
            s = " ".join(b.get("summary") or []).strip()
            new_title = _clean_title(s[:120]) if s else old_title

        old_type = str(b.get("type") or "")
        text = " ".join([old_title, " ".join(b.get("summary") or [])])
        new_type = _infer_type(old_type, text)

        if new_title != old_title or (new_type and new_type != old_type):
            changes.append(
                {
                    "bead_id": bid,
                    "old_title": old_title,
                    "new_title": new_title,
                    "old_type": old_type,
                    "new_type": new_type or old_type,
                }
            )

    if apply and changes:
        for ch in changes:
            b = beads.get(ch["bead_id"]) or {}
            b["title"] = ch["new_title"]
            if ch["new_type"] != ch["old_type"]:
                b["type"] = ch["new_type"]
            b.setdefault("tags", [])
            for tag in ["hygiene_curated"]:
                if tag not in b["tags"]:
                    b["tags"].append(tag)

        atomic_write_json(idx_file, idx)

        ev = root / ".beads" / "events" / "type-title-hygiene.jsonl"
        for ch in changes:
            append_jsonl(
                ev,
                {
                    "event": "type_title_hygiene",
                    "at": _now(),
                    **ch,
                },
            )

    return {"ok": True, "apply": bool(apply), "changes": len(changes), "sample": changes[:50]}
