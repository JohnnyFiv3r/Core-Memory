from __future__ import annotations

from pathlib import Path
from typing import Any

from core_memory.persistence import events
from core_memory.schema.normalization import normalize_relation_type


def association_mix_stats(updates: dict[str, Any] | None) -> dict[str, int]:
    rows = list((updates or {}).get("associations") or []) if isinstance(updates, dict) else []
    total = 0
    shared_tag = 0
    temporal = 0
    semantic = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        total += 1
        rel_raw = str(row.get("relationship") or "").strip().lower()
        rel = normalize_relation_type(rel_raw) if rel_raw else ""
        if rel == "shared_tag":
            shared_tag += 1
        elif rel in {"precedes"}:
            temporal += 1
        elif rel:
            semantic += 1
    return {
        "associations_total": total,
        "shared_tag_count": shared_tag,
        "temporal_count": temporal,
        "non_temporal_semantic_count": semantic,
    }


def emit_agent_turn_quality_metric(
    *,
    root: str,
    req: dict[str, Any],
    gate: dict[str, Any] | None,
    updates: dict[str, Any] | None,
    result: str,
    error_code: str | None = None,
    preview_association_queued: int = 0,
    merge_associations_appended: int = 0,
) -> None:
    gate = dict(gate or {})
    mix = association_mix_stats(updates)
    rec = {
        "run_id": f"turn:{req.get('session_id')}:{req.get('turn_id')}",
        "task_id": "agent_turn_quality",
        "mode": "core_memory",
        "phase": "agent_authored",
        "result": str(result),
        "session_id": str(req.get("session_id") or ""),
        "turn_id": str(req.get("turn_id") or ""),
        "agent_required": bool(gate.get("required")),
        "agent_source": str(gate.get("source") or ""),
        "agent_used_fallback": bool(gate.get("used_fallback")),
        "agent_blocked": bool(gate.get("blocked")),
        "error_code": str(error_code or gate.get("error_code") or "") or None,
        "preview_association_queued": int(preview_association_queued or 0),
        "merge_associations_appended": int(merge_associations_appended or 0),
        **mix,
    }
    try:
        events.append_metric(Path(root), rec)
    except Exception:
        pass
