from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def write_rolling_records(
    workspace_root: str | Path,
    *,
    records: list[dict],
    meta: dict,
    included_bead_ids: list[str],
    excluded_bead_ids: list[str],
) -> str:
    """Canonical rolling continuity authority writer."""
    p = Path(workspace_root) / "rolling-window.records.json"
    payload = {
        "surface": "rolling_window_record_store",
        "authority": "rolling_record_store",
        "role": "runtime_continuity_authority",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "meta": dict(meta or {}),
        "included_bead_ids": [str(x) for x in (included_bead_ids or [])],
        "excluded_bead_ids": [str(x) for x in (excluded_bead_ids or [])],
        "records": [dict(r or {}) for r in (records or [])],
    }
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(p)


def read_rolling_records(workspace_root: str | Path) -> dict:
    p = Path(workspace_root) / "rolling-window.records.json"
    if not p.exists():
        return {
            "surface": "rolling_window_record_store",
            "authority": "rolling_record_store",
            "role": "runtime_continuity_authority",
            "records": [],
        }
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload.setdefault("authority", "rolling_record_store")
            payload.setdefault("role", "runtime_continuity_authority")
            return payload
    except Exception:
        pass
    return {
        "surface": "rolling_window_record_store",
        "authority": "rolling_record_store",
        "role": "runtime_continuity_authority",
        "records": [],
    }
