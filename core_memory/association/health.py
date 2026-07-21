from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.schema.normalization import relation_family

STRUCTURAL_CONTINUITY_RELATIONSHIPS = frozenset({"follows", "precedes", "part_of"})
PENDING_JUDGE_WARNING_SECONDS = 5 * 60
PENDING_JUDGE_CRITICAL_SECONDS = 60 * 60


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def association_pending_judge_health(
    root: str,
    *,
    session_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    runs_path = Path(root) / ".beads" / "events" / "association-runs.jsonl"
    latest_by_run: dict[str, dict[str, Any]] = {}
    if runs_path.exists():
        for line in runs_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            run_id = str(row.get("run_id") or "").strip()
            if run_id:
                latest_by_run[run_id] = row

    index_path = Path(root) / ".beads" / "index.json"
    beads: dict[str, dict[str, Any]] = {}
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            index = {}
        beads = {
            str(key): dict(value)
            for key, value in ((index.get("beads") or {}).items() if isinstance(index, dict) else [])
            if isinstance(value, dict)
        }

    now_value = now or datetime.now(timezone.utc)
    if now_value.tzinfo is None:
        now_value = now_value.replace(tzinfo=timezone.utc)
    pending_runs: list[dict[str, Any]] = []
    for run_id, row in latest_by_run.items():
        if str(row.get("status") or "").strip().lower() != "pending_judge":
            continue
        row_session = str(row.get("session_id") or "").strip()
        bead_ids = [str(value).strip() for value in (row.get("bead_ids") or []) if str(value).strip()]
        bead_sessions = {
            str((beads.get(bead_id) or {}).get("session_id") or "").strip()
            for bead_id in bead_ids
            if str((beads.get(bead_id) or {}).get("session_id") or "").strip()
        }
        if session_id and row_session != str(session_id) and str(session_id) not in bead_sessions:
            continue
        recorded_at = _parse_timestamp(row.get("recorded_at"))
        age_seconds = max(0.0, (now_value - recorded_at).total_seconds()) if recorded_at else 0.0
        turn_ids = list(
            dict.fromkeys(
                str(turn_id).strip()
                for bead_id in bead_ids
                for turn_id in ((beads.get(bead_id) or {}).get("source_turn_ids") or [])
                if str(turn_id).strip()
            )
        )
        pending_runs.append(
            {
                "run_id": run_id,
                "session_id": row_session or (next(iter(bead_sessions)) if len(bead_sessions) == 1 else None),
                "bead_ids": bead_ids,
                "turn_ids": turn_ids,
                "recorded_at": str(row.get("recorded_at") or "") or None,
                "age_seconds": round(age_seconds, 3),
                "warning": str(row.get("warning") or "") or None,
            }
        )
    pending_runs.sort(key=lambda row: float(row.get("age_seconds") or 0.0), reverse=True)
    oldest = float((pending_runs[0] if pending_runs else {}).get("age_seconds") or 0.0)
    severity = (
        "critical"
        if oldest >= PENDING_JUDGE_CRITICAL_SECONDS
        else "warning"
        if oldest >= PENDING_JUDGE_WARNING_SECONDS
        else "ok"
    )
    return {
        "pending_judge_count": len(pending_runs),
        "oldest_pending_judge_age_seconds": round(oldest, 3),
        "severity": severity,
        "warning_after_seconds": PENDING_JUDGE_WARNING_SECONDS,
        "critical_after_seconds": PENDING_JUDGE_CRITICAL_SECONDS,
        "pending_judge_runs": pending_runs,
    }


def association_health_report(root: str, *, session_id: str | None = None) -> dict[str, Any]:
    idx_file = Path(root) / ".beads" / "index.json"
    if not idx_file.exists():
        return {"ok": False, "error": "index_missing"}

    try:
        idx = json.loads(idx_file.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": False, "error": "index_read_failed"}

    beads = {str(k): dict(v) for k, v in ((idx.get("beads") or {}).items()) if isinstance(v, dict)}
    assocs = [a for a in (idx.get("associations") or []) if isinstance(a, dict)]

    if session_id:
        sid = str(session_id)
        scope_ids = {bid for bid, b in beads.items() if str(b.get("session_id") or "") == sid}
        scoped = []
        for a in assocs:
            s = str(a.get("source_bead") or a.get("source_bead_id") or "")
            t = str(a.get("target_bead") or a.get("target_bead_id") or "")
            if s in scope_ids or t in scope_ids:
                scoped.append(a)
        assocs = scoped
        beads = {bid: b for bid, b in beads.items() if bid in scope_ids}

    rel = Counter()
    rel_active = Counter()
    structural_active = Counter()
    semantic_active = Counter()
    semantic_causal_active = Counter()
    status = Counter()
    deg = defaultdict(int)
    for a in assocs:
        r = str(a.get("relationship") or "").strip().lower() or "unknown"
        st = str(a.get("status") or "active").strip().lower() or "active"
        rel[r] += 1
        status[st] += 1
        if st in {"retracted", "superseded", "inactive"}:
            continue
        rel_active[r] += 1
        if r in STRUCTURAL_CONTINUITY_RELATIONSHIPS:
            structural_active[r] += 1
        else:
            semantic_active[r] += 1
            if relation_family(r) in {"causal", "evidence", "influence", "conflict", "revision"}:
                semantic_causal_active[r] += 1
        s = str(a.get("source_bead") or a.get("source_bead_id") or "")
        t = str(a.get("target_bead") or a.get("target_bead_id") or "")
        if s:
            deg[s] += 1
        if t:
            deg[t] += 1

    active_assocs = int(sum(v for k, v in status.items() if k not in {"retracted", "superseded", "inactive"}))
    isolated = sum(1 for bid in beads if deg.get(bid, 0) == 0)

    noise_rels = {"shared_tag", "follows", "precedes"}
    active_noise = sum(v for k, v in rel_active.items() if k in noise_rels)

    pending = association_pending_judge_health(root, session_id=session_id)
    return {
        "ok": True,
        "session_id": str(session_id or "") or None,
        "beads": len(beads),
        "associations_total": len(assocs),
        "associations_active": active_assocs,
        "status_distribution": dict(status),
        "relationship_top": rel.most_common(20),
        "relationship_top_active": rel_active.most_common(20),
        "structural_continuity_active": sum(structural_active.values()),
        "semantic_relationships_active": sum(semantic_active.values()),
        "semantic_causal_active": sum(semantic_causal_active.values()),
        "structural_relationship_top_active": structural_active.most_common(20),
        "semantic_relationship_top_active": semantic_active.most_common(20),
        "semantic_causal_top_active": semantic_causal_active.most_common(20),
        "isolated_beads": isolated,
        "isolated_pct": round((isolated / max(1, len(beads))) * 100.0, 2),
        "active_noise_pct": round((active_noise / max(1, active_assocs)) * 100.0, 2),
        **pending,
    }
