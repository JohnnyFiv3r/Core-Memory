from __future__ import annotations

import json
from pathlib import Path

from core_memory.rolling_record_store import read_rolling_records


def load_continuity_injection(workspace_root: str | Path, max_items: int = 80) -> dict:
    """Load continuity injection context with rolling record store as authority.

    Authority order:
    1) rolling-window.records.json
    2) promoted-context.meta.json fallback
    3) empty
    """
    rr = read_rolling_records(workspace_root)
    recs = list(rr.get("records") or [])
    if recs:
        return {
            "authority": "rolling_record_store",
            "records": recs[: max(1, int(max_items))],
            "included_bead_ids": list(rr.get("included_bead_ids") or []),
            "meta": dict(rr.get("meta") or {}),
        }

    meta_path = Path(workspace_root) / "promoted-context.meta.json"
    if meta_path.exists():
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        return {
            "authority": "promoted_context_meta_fallback",
            "records": [],
            "included_bead_ids": list(payload.get("included_bead_ids") or []),
            "meta": dict(payload.get("meta") or {}),
        }

    return {
        "authority": "none",
        "records": [],
        "included_bead_ids": [],
        "meta": {},
    }
