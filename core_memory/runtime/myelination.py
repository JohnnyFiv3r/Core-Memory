from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from core_memory.runtime.retrieval_feedback import read_retrieval_feedback


def myelination_enabled() -> bool:
    raw = str(os.getenv("CORE_MEMORY_MYELINATION_ENABLED", "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return float(default)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except Exception:
        return int(default)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def compute_myelination_bonus_map(
    root: str | Path,
    *,
    since: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    since_v = str(since or os.getenv("CORE_MEMORY_MYELINATION_SINCE", "30d"))
    limit_v = int(limit or _int_env("CORE_MEMORY_MYELINATION_LIMIT", 1000))

    if not myelination_enabled():
        return {
            "enabled": False,
            "bonus_by_bead_id": {},
            "stats": {"events": 0, "beads": 0, "strengthened": 0, "weakened": 0},
            "config": {"since": since_v, "limit": limit_v},
        }

    min_hits = max(1, _int_env("CORE_MEMORY_MYELINATION_MIN_HITS", 2))
    cap_pos = max(0.0, _float_env("CORE_MEMORY_MYELINATION_POS_CAP", 0.12))
    cap_neg = max(0.0, _float_env("CORE_MEMORY_MYELINATION_NEG_CAP", 0.08))

    rows = read_retrieval_feedback(root, since=since_v, limit=max(1, limit_v))

    stats: dict[str, dict[str, int]] = {}
    for row in rows:
        resp = dict(row.get("response") or {})
        bid_list = [str(x or "").strip() for x in (resp.get("result_bead_ids") or [])]
        bid_list = [x for x in dict.fromkeys(bid_list) if x]
        if not bid_list:
            continue
        success = bool(row.get("success"))
        for bid in bid_list:
            s = stats.setdefault(bid, {"success": 0, "fail": 0})
            if success:
                s["success"] += 1
            else:
                s["fail"] += 1

    out: dict[str, float] = {}
    strengthened = 0
    weakened = 0

    for bid, sf in stats.items():
        succ = int(sf.get("success") or 0)
        fail = int(sf.get("fail") or 0)
        total = succ + fail
        if total < min_hits:
            continue

        success_rate = succ / float(total)
        fail_rate = fail / float(total)

        # No time decay: evidence is purely telemetry-driven from retrieval outcomes.
        evidence = min(1.0, math.log1p(float(total)) / math.log1p(8.0))
        raw_bonus = evidence * ((cap_pos * success_rate) - (cap_neg * fail_rate))
        bonus = _clamp(raw_bonus, -cap_neg, cap_pos)

        if abs(bonus) < 1e-9:
            continue
        out[bid] = round(float(bonus), 6)
        if bonus > 0:
            strengthened += 1
        elif bonus < 0:
            weakened += 1

    return {
        "enabled": True,
        "bonus_by_bead_id": out,
        "stats": {
            "events": len(rows),
            "beads": len(out),
            "strengthened": strengthened,
            "weakened": weakened,
        },
        "config": {
            "since": since_v,
            "limit": max(1, limit_v),
            "min_hits": min_hits,
            "pos_cap": cap_pos,
            "neg_cap": cap_neg,
        },
    }


__all__ = [
    "myelination_enabled",
    "compute_myelination_bonus_map",
]


def myelination_report(
    root: str | Path,
    *,
    since: str | None = None,
    limit: int | None = None,
    top: int = 20,
) -> dict[str, Any]:
    payload = compute_myelination_bonus_map(root, since=since, limit=limit)
    bonus = dict(payload.get("bonus_by_bead_id") or {})

    pos = sorted(
        [{"bead_id": bid, "bonus": float(v)} for bid, v in bonus.items() if float(v) > 0.0],
        key=lambda r: float(r.get("bonus") or 0.0),
        reverse=True,
    )
    neg = sorted(
        [{"bead_id": bid, "bonus": float(v)} for bid, v in bonus.items() if float(v) < 0.0],
        key=lambda r: float(r.get("bonus") or 0.0),
    )

    return {
        "schema": "core_memory.myelination_experiment.v1",
        "enabled": bool(payload.get("enabled")),
        "stats": dict(payload.get("stats") or {}),
        "config": dict(payload.get("config") or {}),
        "top_strengthened": pos[: max(1, int(top))],
        "top_weakened": neg[: max(1, int(top))],
    }


__all__.append("myelination_report")
