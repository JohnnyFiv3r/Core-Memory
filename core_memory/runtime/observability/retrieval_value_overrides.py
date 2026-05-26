from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.persistence.io_utils import append_jsonl, store_lock


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _index_path(root: str | Path) -> Path:
    return Path(root) / ".beads" / "index.json"


def _events_path(root: str | Path) -> Path:
    return Path(root) / ".beads" / "events" / "retrieval-value-overrides.jsonl"


def _read_index(root: str | Path) -> dict[str, Any]:
    p = _index_path(root)
    if not p.exists():
        return {"beads": {}, "associations": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"beads": {}, "associations": []}
    except Exception:
        return {"beads": {}, "associations": []}


def _write_index(root: str | Path, idx: dict[str, Any]) -> None:
    p = _index_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(idx, indent=2), encoding="utf-8")
    tmp.replace(p)


def ensure_retrieval_value_overrides_for_index(index: dict[str, Any]) -> None:
    rows = index.get("retrieval_value_overrides")
    if not isinstance(rows, dict):
        index["retrieval_value_overrides"] = {}


def _override_id(source_bead_id: str, target_bead_id: str, relationship: str, source_proposal_id: str) -> str:
    base = f"{source_bead_id}|{target_bead_id}|{relationship}|{source_proposal_id}"
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
    return f"rvo-{digest}"


def apply_retrieval_value_override_for_index(
    index: dict[str, Any],
    *,
    source_bead_id: str,
    target_bead_id: str,
    relationship: str,
    proposed_weight_delta: float,
    reviewer: str = "",
    notes: str = "",
    source_proposal_id: str = "",
) -> dict[str, Any]:
    ensure_retrieval_value_overrides_for_index(index)

    src = str(source_bead_id or "").strip()
    tgt = str(target_bead_id or "").strip()
    rel = str(relationship or "").strip() or "supports"
    if not src or not tgt:
        return {"ok": False, "error": "missing_source_or_target"}

    beads = index.get("beads") or {}
    if src not in beads or tgt not in beads:
        return {"ok": False, "error": "bead_not_found"}

    delta = float(proposed_weight_delta or 0.0)
    delta = max(-0.5, min(0.5, delta))

    proposal_id = str(source_proposal_id or "").strip()
    oid = _override_id(src, tgt, rel, proposal_id)
    rows = index.get("retrieval_value_overrides") or {}
    row = dict(rows.get(oid) or {})

    if not row:
        row = {
            "id": oid,
            "source_bead_id": src,
            "target_bead_id": tgt,
            "relationship": rel,
            "weight_delta": round(delta, 4),
            "status": "active",
            "created_at": _now(),
            "updated_at": _now(),
            "reviewer": str(reviewer or ""),
            "notes": str(notes or ""),
            "source_proposal_id": proposal_id,
            "apply_count": 1,
        }
    else:
        row["weight_delta"] = round(float(row.get("weight_delta") or 0.0) + delta, 4)
        row["weight_delta"] = max(-0.75, min(0.75, float(row.get("weight_delta") or 0.0)))
        row["updated_at"] = _now()
        row["reviewer"] = str(reviewer or row.get("reviewer") or "")
        row["notes"] = str(notes or row.get("notes") or "")
        row["source_proposal_id"] = proposal_id or str(row.get("source_proposal_id") or "")
        row["apply_count"] = int(row.get("apply_count") or 0) + 1
        row["status"] = "active"

    rows[oid] = row
    index["retrieval_value_overrides"] = rows
    return {"ok": True, "override": row}


def list_retrieval_value_overrides_for_index(index: dict[str, Any], *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    ensure_retrieval_value_overrides_for_index(index)
    rows = [dict(v or {}) for v in (index.get("retrieval_value_overrides") or {}).values() if isinstance(v, dict)]
    if status:
        s = str(status).strip().lower()
        rows = [r for r in rows if str(r.get("status") or "").strip().lower() == s]
    rows.sort(key=lambda r: (str(r.get("status") or ""), -abs(float(r.get("weight_delta") or 0.0)), str(r.get("id") or "")))
    return rows[: max(1, int(limit))]


def apply_retrieval_value_override(
    root: str | Path,
    *,
    source_bead_id: str,
    target_bead_id: str,
    relationship: str,
    proposed_weight_delta: float,
    reviewer: str = "",
    notes: str = "",
    source_proposal_id: str = "",
) -> dict[str, Any]:
    with store_lock(Path(root)):
        idx = _read_index(root)
        out = apply_retrieval_value_override_for_index(
            idx,
            source_bead_id=source_bead_id,
            target_bead_id=target_bead_id,
            relationship=relationship,
            proposed_weight_delta=proposed_weight_delta,
            reviewer=reviewer,
            notes=notes,
            source_proposal_id=source_proposal_id,
        )
        _write_index(root, idx)

        if out.get("ok"):
            append_jsonl(
                _events_path(root),
                {
                    "id": str((out.get("override") or {}).get("id") or ""),
                    "created_at": _now(),
                    "kind": "retrieval_value_override_apply",
                    "source_bead_id": str(source_bead_id or ""),
                    "target_bead_id": str(target_bead_id or ""),
                    "relationship": str(relationship or ""),
                    "weight_delta": float(((out.get("override") or {}).get("weight_delta") or 0.0)),
                    "reviewer": str(reviewer or ""),
                    "notes": str(notes or ""),
                    "source_proposal_id": str(source_proposal_id or ""),
                },
            )

        return out


def list_retrieval_value_overrides(root: str | Path, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    idx = _read_index(root)
    return list_retrieval_value_overrides_for_index(idx, status=status, limit=limit)
