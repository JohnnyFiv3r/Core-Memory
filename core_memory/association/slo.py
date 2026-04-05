from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import json
import re

from core_memory.persistence import events


def _window_start(since: str | None) -> datetime | None:
    s = str(since or "").strip().lower()
    if not s:
        return None
    m = re.fullmatch(r"(\d+)([dh])", s)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    delta = timedelta(days=n) if unit == "d" else timedelta(hours=n)
    return datetime.now(timezone.utc) - delta


def _in_window(ts: str | None, ws: datetime | None) -> bool:
    if ws is None:
        return True
    if not ts:
        return True
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return True
    return dt >= ws


def _active_shared_tag_ratio(root: str) -> float:
    idx_file = Path(root) / ".beads" / "index.json"
    if not idx_file.exists():
        return 0.0
    try:
        idx = json.loads(idx_file.read_text(encoding="utf-8"))
    except Exception:
        return 0.0

    active_total = 0
    shared_tag = 0
    for a in (idx.get("associations") or []):
        if not isinstance(a, dict):
            continue
        status = str(a.get("status") or "active").strip().lower() or "active"
        if status in {"retracted", "superseded", "inactive"}:
            continue
        active_total += 1
        rel = str(a.get("relationship") or "").strip().lower()
        if rel == "shared_tag":
            shared_tag += 1
    if active_total <= 0:
        return 0.0
    return float(shared_tag) / float(active_total)


def association_slo_report(root: str, *, since: str = "7d") -> dict[str, Any]:
    ws = _window_start(since)
    rows = []
    for r in (events.iter_metrics(Path(root)) or []):
        if str(r.get("task_id") or "") != "agent_turn_quality":
            continue
        if not _in_window(str(r.get("ts") or ""), ws):
            continue
        rows.append(dict(r))

    turns = len(rows)
    if turns <= 0:
        return {
            "ok": True,
            "since": since,
            "turns": 0,
            "agent_authored_rate": 0.0,
            "fallback_rate": 0.0,
            "fail_closed_rate": 0.0,
            "avg_non_temporal_semantic": 0.0,
            "active_shared_tag_ratio": round(_active_shared_tag_ratio(root), 4),
            "agent_source_counts": {},
        }

    source_counts = Counter(str(r.get("agent_source") or "") for r in rows)
    agent_authored = sum(
        1 for r in rows if str(r.get("agent_source") or "") in {"metadata.crawler_updates", "agent_callable"}
    )
    fallback = sum(1 for r in rows if bool(r.get("agent_used_fallback")))
    blocked = sum(1 for r in rows if bool(r.get("agent_blocked")))

    semantic_vals = [int(r.get("non_temporal_semantic_count") or 0) for r in rows if str(r.get("result") or "") == "success"]
    avg_sem = (sum(semantic_vals) / len(semantic_vals)) if semantic_vals else 0.0

    return {
        "ok": True,
        "since": since,
        "turns": turns,
        "agent_authored_rate": round(agent_authored / turns, 4),
        "fallback_rate": round(fallback / turns, 4),
        "fail_closed_rate": round(blocked / turns, 4),
        "avg_non_temporal_semantic": round(avg_sem, 4),
        "active_shared_tag_ratio": round(_active_shared_tag_ratio(root), 4),
        "agent_source_counts": dict(source_counts),
    }


def association_slo_check(
    root: str,
    *,
    since: str = "7d",
    min_agent_authored_rate: float = 0.8,
    max_fallback_rate: float = 0.1,
    max_fail_closed_rate: float = 0.25,
    min_avg_non_temporal_semantic: float = 1.0,
    max_active_shared_tag_ratio: float = 0.4,
) -> dict[str, Any]:
    report = association_slo_report(root, since=since)
    violations: list[dict[str, Any]] = []

    def check_min(name: str, value: float, threshold: float):
        if value < threshold:
            violations.append({"metric": name, "value": round(value, 4), "threshold": threshold, "op": ">="})

    def check_max(name: str, value: float, threshold: float):
        if value > threshold:
            violations.append({"metric": name, "value": round(value, 4), "threshold": threshold, "op": "<="})

    turns = int(report.get("turns") or 0)
    if turns > 0:
        check_min("agent_authored_rate", float(report.get("agent_authored_rate") or 0.0), float(min_agent_authored_rate))
        check_max("fallback_rate", float(report.get("fallback_rate") or 0.0), float(max_fallback_rate))
        check_max("fail_closed_rate", float(report.get("fail_closed_rate") or 0.0), float(max_fail_closed_rate))
        check_min(
            "avg_non_temporal_semantic",
            float(report.get("avg_non_temporal_semantic") or 0.0),
            float(min_avg_non_temporal_semantic),
        )
    check_max(
        "active_shared_tag_ratio",
        float(report.get("active_shared_tag_ratio") or 0.0),
        float(max_active_shared_tag_ratio),
    )

    return {
        "ok": len(violations) == 0,
        "violations": violations,
        "thresholds": {
            "min_agent_authored_rate": float(min_agent_authored_rate),
            "max_fallback_rate": float(max_fallback_rate),
            "max_fail_closed_rate": float(max_fail_closed_rate),
            "min_avg_non_temporal_semantic": float(min_avg_non_temporal_semantic),
            "max_active_shared_tag_ratio": float(max_active_shared_tag_ratio),
        },
        "report": report,
    }
