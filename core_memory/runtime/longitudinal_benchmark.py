from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _parse_iso(value: str | None) -> datetime | None:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _parse_since(since: str | None) -> timedelta | None:
    raw = str(since or "").strip().lower()
    if not raw:
        return None
    m = re.fullmatch(r"(\d+)\s*([dh])", raw)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    if unit == "d":
        return timedelta(days=n)
    if unit == "h":
        return timedelta(hours=n)
    return None


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _load_candidates(root: Path) -> list[dict[str, Any]]:
    p = root / ".beads" / "events" / "dreamer-candidates.json"
    payload = _read_json(p, [])
    if not isinstance(payload, list):
        return []
    return [x for x in payload if isinstance(x, dict)]


def _load_beads(root: Path) -> dict[str, dict[str, Any]]:
    p = root / ".beads" / "index.json"
    payload = _read_json(p, {})
    beads = (payload.get("beads") or {}) if isinstance(payload, dict) else {}
    if not isinstance(beads, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in beads.items():
        if isinstance(v, dict):
            out[str(k)] = dict(v)
    return out


def _signal_names(candidate: dict[str, Any]) -> set[str]:
    raw = (candidate.get("raw") or {}) if isinstance(candidate.get("raw"), dict) else {}
    signals = raw.get("structural_signals") or []
    out: set[str] = set()
    if isinstance(signals, list):
        for s in signals:
            if isinstance(s, dict):
                n = str(s.get("name") or "").strip()
                if n:
                    out.add(n)
    return out


def _has_structural_signal(candidate: dict[str, Any]) -> bool:
    names = _signal_names(candidate)
    if names:
        return True
    raw = (candidate.get("raw") or {}) if isinstance(candidate.get("raw"), dict) else {}
    score = float(raw.get("structural_score") or 0.0)
    if score >= 0.25:
        return True
    rel = str(candidate.get("relationship") or "").strip().lower()
    if rel in {"structural_symmetry", "transferable_lesson", "contradicts"}:
        return True
    return False


def _is_cross_session(beads: dict[str, dict[str, Any]], candidate: dict[str, Any]) -> bool:
    src = str(candidate.get("source_bead_id") or "")
    tgt = str(candidate.get("target_bead_id") or "")
    if not src or not tgt:
        return False
    s1 = str((beads.get(src) or {}).get("session_id") or "")
    s2 = str((beads.get(tgt) or {}).get("session_id") or "")
    return bool(s1 and s2 and s1 != s2)


def _status(candidate: dict[str, Any]) -> str:
    return str(candidate.get("status") or "").strip().lower()


def _accepted(candidate: dict[str, Any]) -> bool:
    return _status(candidate) == "accepted"


def _decided(candidate: dict[str, Any]) -> bool:
    return _status(candidate) in {"accepted", "rejected"}


def _accepted_applied(candidate: dict[str, Any]) -> bool:
    if not _accepted(candidate):
        return False
    d = candidate.get("decision") if isinstance(candidate.get("decision"), dict) else {}
    return bool(
        str(d.get("applied_association_id") or "").strip()
        or str(d.get("applied_turn_id") or "").strip()
    )


def _downstream_used(beads: dict[str, dict[str, Any]], candidate: dict[str, Any]) -> bool:
    src = str(candidate.get("source_bead_id") or "")
    tgt = str(candidate.get("target_bead_id") or "")
    r1 = int((beads.get(src) or {}).get("recall_count") or 0)
    r2 = int((beads.get(tgt) or {}).get("recall_count") or 0)
    return (r1 + r2) > 0


def _safe_rate(n: int, d: int) -> float:
    return float(n) / float(d) if d > 0 else 0.0


def _cohort_metrics(rows: list[dict[str, Any]], beads: dict[str, dict[str, Any]], *, applied_only: bool = False) -> dict[str, Any]:
    base = [r for r in rows if not applied_only or _accepted_applied(r)]
    decided = [r for r in base if _decided(r)]
    accepted = [r for r in base if _accepted(r)]
    transfer = [r for r in base if _is_cross_session(beads, r)]
    transfer_accepted = [r for r in transfer if _accepted(r)]
    accepted_used = [r for r in accepted if _downstream_used(beads, r)]

    accepted_rate = _safe_rate(len(accepted), len(decided))
    transfer_success = _safe_rate(len(transfer_accepted), len(transfer))
    downstream_use_rate = _safe_rate(len(accepted_used), len(accepted))

    quality_score = (
        0.40 * accepted_rate
        + 0.35 * transfer_success
        + 0.25 * downstream_use_rate
    )

    return {
        "counts": {
            "total": len(base),
            "decided": len(decided),
            "accepted": len(accepted),
            "cross_session_transfer": len(transfer),
            "accepted_with_downstream_use": len(accepted_used),
            "accepted_applied": sum(1 for r in base if _accepted_applied(r)),
        },
        "rates": {
            "accepted_rate": accepted_rate,
            "cross_session_transfer_success_rate": transfer_success,
            "downstream_use_rate": downstream_use_rate,
            "quality_score": quality_score,
        },
    }


def longitudinal_benchmark_v2(root: str | Path, *, since: str = "30d") -> dict[str, Any]:
    root_p = Path(root)
    candidates = _load_candidates(root_p)
    beads = _load_beads(root_p)

    cutoff = None
    delta = _parse_since(since)
    if delta is not None:
        cutoff = datetime.now(timezone.utc) - delta

    scoped: list[dict[str, Any]] = []
    for c in candidates:
        if cutoff is not None:
            dt = _parse_iso(str(c.get("created_at") or ""))
            if dt is not None and dt < cutoff:
                continue
        scoped.append(c)

    summary_only = [r for r in scoped if not _has_structural_signal(r)]
    with_dreamer = [r for r in scoped if _has_structural_signal(r)]

    no_memory = {
        "counts": {
            "total": 0,
            "decided": 0,
            "accepted": 0,
            "cross_session_transfer": 0,
            "accepted_with_downstream_use": 0,
            "accepted_applied": 0,
        },
        "rates": {
            "accepted_rate": 0.0,
            "cross_session_transfer_success_rate": 0.0,
            "downstream_use_rate": 0.0,
            "quality_score": 0.0,
        },
    }

    summary_metrics = _cohort_metrics(summary_only, beads)
    core_no_dreamer_metrics = _cohort_metrics(scoped, beads)
    core_with_dreamer_metrics = _cohort_metrics(with_dreamer, beads, applied_only=True)

    no_mem_score = float((no_memory.get("rates") or {}).get("quality_score") or 0.0)
    summary_score = float((summary_metrics.get("rates") or {}).get("quality_score") or 0.0)
    core_no_score = float((core_no_dreamer_metrics.get("rates") or {}).get("quality_score") or 0.0)
    core_yes_score = float((core_with_dreamer_metrics.get("rates") or {}).get("quality_score") or 0.0)

    report = {
        "schema": "core_memory.longitudinal_benchmark_v2.v1",
        "root": str(root_p),
        "since": since,
        "cohorts": {
            "no_memory_baseline": no_memory,
            "summary_only_baseline": summary_metrics,
            "core_memory_without_dreamer": core_no_dreamer_metrics,
            "core_memory_with_dreamer": core_with_dreamer_metrics,
        },
        "comparisons": {
            "summary_vs_no_memory_lift": summary_score - no_mem_score,
            "core_without_dreamer_vs_summary_lift": core_no_score - summary_score,
            "core_with_dreamer_vs_core_without_dreamer_lift": core_yes_score - core_no_score,
            "core_with_dreamer_vs_no_memory_lift": core_yes_score - no_mem_score,
        },
        "diagnostics": {
            "total_candidates_scoped": len(scoped),
            "summary_only_candidates": len(summary_only),
            "dreamer_structural_candidates": len(with_dreamer),
        },
    }
    return report


__all__ = ["longitudinal_benchmark_v2"]
