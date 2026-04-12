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


def _edge_key(src: str, dst: str, rel: str) -> str:
    return f"{src}|{rel}|{dst}"


def _edge_key_parts(key: str) -> tuple[str, str, str]:
    src, rel, dst = (str(key or "").split("|", 2) + ["", "", ""])[:3]
    return src, rel, dst


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
            "bonus_by_edge_key": {},
            "bonus_by_bead_id": {},
            "stats": {"events": 0, "edges": 0, "beads": 0, "strengthened": 0, "weakened": 0},
            "config": {"since": since_v, "limit": limit_v},
        }

    min_hits = max(1, _int_env("CORE_MEMORY_MYELINATION_MIN_HITS", 2))
    cap_pos = max(0.0, _float_env("CORE_MEMORY_MYELINATION_POS_CAP", 0.12))
    cap_neg = max(0.0, _float_env("CORE_MEMORY_MYELINATION_NEG_CAP", 0.08))

    rows = read_retrieval_feedback(root, since=since_v, limit=max(1, limit_v))

    # Edge-first learning surface (PRD-aligned): learn useful traversal paths, not node decay.
    edge_stats: dict[str, dict[str, int]] = {}
    for row in rows:
        resp = dict(row.get("response") or {})
        edges = []
        for e in (resp.get("edges") or []):
            if not isinstance(e, dict):
                continue
            src = str(e.get("src") or "").strip()
            dst = str(e.get("dst") or "").strip()
            rel = str(e.get("rel") or "").strip()
            if not src or not dst or not rel:
                continue
            edges.append(_edge_key(src, dst, rel))
        edge_keys = [x for x in dict.fromkeys(edges) if x]
        if not edge_keys:
            continue
        success = bool(row.get("success"))
        for ek in edge_keys:
            s = edge_stats.setdefault(ek, {"success": 0, "fail": 0})
            if success:
                s["success"] += 1
            else:
                s["fail"] += 1

    edge_bonus: dict[str, float] = {}
    bead_bonus: dict[str, float] = {}
    strengthened = 0
    weakened = 0

    for ek, sf in edge_stats.items():
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
        edge_bonus[ek] = round(float(bonus), 6)

        # Runtime scoring still consumes bead-level signals; project edge learning onto endpoints.
        src, _, dst = _edge_key_parts(ek)
        share = float(bonus) * 0.5
        if src:
            bead_bonus[src] = bead_bonus.get(src, 0.0) + share
        if dst:
            bead_bonus[dst] = bead_bonus.get(dst, 0.0) + share

        if bonus > 0:
            strengthened += 1
        elif bonus < 0:
            weakened += 1

    # Keep projected bead bonus bounded for scorer stability.
    bead_bonus = {k: round(_clamp(float(v), -cap_neg, cap_pos), 6) for k, v in bead_bonus.items() if abs(float(v)) > 1e-9}

    return {
        "enabled": True,
        "bonus_by_edge_key": edge_bonus,
        "bonus_by_bead_id": bead_bonus,
        "stats": {
            "events": len(rows),
            "edges": len(edge_bonus),
            "beads": len(bead_bonus),
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
    edge_bonus = dict(payload.get("bonus_by_edge_key") or {})
    bonus = dict(payload.get("bonus_by_bead_id") or {})

    edge_pos = sorted(
        [{"edge_key": k, "bonus": float(v)} for k, v in edge_bonus.items() if float(v) > 0.0],
        key=lambda r: float(r.get("bonus") or 0.0),
        reverse=True,
    )
    edge_neg = sorted(
        [{"edge_key": k, "bonus": float(v)} for k, v in edge_bonus.items() if float(v) < 0.0],
        key=lambda r: float(r.get("bonus") or 0.0),
    )

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
        "top_edge_strengthened": edge_pos[: max(1, int(top))],
        "top_edge_weakened": edge_neg[: max(1, int(top))],
        "top_strengthened": pos[: max(1, int(top))],
        "top_weakened": neg[: max(1, int(top))],
    }


__all__.append("myelination_report")
