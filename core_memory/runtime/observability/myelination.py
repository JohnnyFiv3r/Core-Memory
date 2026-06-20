from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

MYELINATION_MANIFEST_SCHEMA = "core_memory.myelination_manifest.v2"


def _manifest_path(root: str | Path) -> Path:
    return Path(root) / ".beads" / "events" / "myelination-manifest.json"


def read_myelination_manifest(root: str | Path) -> dict[str, Any]:
    """Serve the myelination manifest from disk (never recomputed on read).

    The manifest is rebuilt on the maintenance cadence (the ``myelination-update``
    side-effect job / Dreamer pass). Returns ``present=False`` when none exists
    yet so a host knows to trigger a refresh, rather than computing inline.
    """
    p = _manifest_path(root)
    if not p.exists():
        return {
            "ok": True,
            "present": False,
            "schema": MYELINATION_MANIFEST_SCHEMA,
            "enabled": myelination_enabled(),
            "note": "no myelination manifest yet; run a myelination-update to build it",
        }
    try:
        manifest = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(manifest, dict):
            return {"ok": True, "present": True, **manifest}
    except Exception:
        pass
    return {
        "ok": False,
        "present": False,
        "error": "myelination_manifest_unreadable",
        "schema": MYELINATION_MANIFEST_SCHEMA,
    }

from core_memory.runtime.observability.retrieval_feedback import read_retrieval_feedback
from core_memory.schema.normalization import normalize_relation_type


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


def _project_bead_bonus(edge_bonus: dict[str, float], cap_neg: float, cap_pos: float) -> dict[str, float]:
    """Project edge learning onto endpoint beads for scorer compatibility.

    Bead bonus is a *projection* of edge bonus, never bead decay.
    """
    bead_bonus: dict[str, float] = {}
    for ek, bonus in edge_bonus.items():
        src, _, dst = _edge_key_parts(ek)
        share = float(bonus) * 0.5
        if src:
            bead_bonus[src] = bead_bonus.get(src, 0.0) + share
        if dst:
            bead_bonus[dst] = bead_bonus.get(dst, 0.0) + share
    return {
        k: round(_clamp(float(v), -cap_neg, cap_pos), 6)
        for k, v in bead_bonus.items()
        if abs(float(v)) > 1e-9
    }


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
            "schema": "core_memory.myelination_manifest.v2",
            "enabled": False,
            "bonus_by_edge_key": {},
            "bonus_by_bead_id": {},
            "has_validated_tier_by_edge_key": {},
            "validated_reward_counts_by_edge_key": {},
            "stats": {"events": 0, "edges": 0, "beads": 0, "strengthened": 0, "weakened": 0},
            "source_event_counts": {},
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
            raw_rel = str(e.get("rel") or "").strip()
            if not src or not dst or not raw_rel:
                continue
            # Normalize the relation so feedback edges share canonical keys with
            # reward events (and with what consumers query) — otherwise a legacy
            # "Causes" feedback edge and a "caused_by" reward never fuse.
            edges.append(_edge_key(src, dst, normalize_relation_type(raw_rel)))
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

    # Telemetry layer: feedback-driven edge bonus (min-hits gates noisy telemetry).
    edge_bonus: dict[str, float] = {}
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

    # Reward layer (V2): audited reward/decay events fuse additively per edge.
    # Audited events bypass the telemetry min-hits filter — they are explicit, not
    # noisy — but are still cap-clamped. bonus_by_bead_id stays a projection.
    source_event_counts: dict[str, int] = {}
    validated_edge_keys: dict[str, bool] = {}
    validated_positive_counts: dict[str, int] = {}
    validated_negative_counts: dict[str, int] = {}
    # Lazy import avoids a module cycle (rewards imports from this module).
    from core_memory.runtime.observability.myelination_rewards import (
        reward_bonus_by_edge_key,
        reward_events_enabled,
    )

    rewards_on = reward_events_enabled()
    if rewards_on:
        reward_limit = _int_env("CORE_MEMORY_MYELINATION_REWARD_EVENT_LIMIT", 2000)
        reward = reward_bonus_by_edge_key(root, since=since_v, limit=reward_limit)
        for ek, info in reward.items():
            base = float(edge_bonus.get(ek, 0.0))
            fused = _clamp(base + float(info.get("bonus") or 0.0), -cap_neg, cap_pos)
            if abs(fused) < 1e-9:
                edge_bonus.pop(ek, None)
            else:
                edge_bonus[ek] = round(fused, 6)
            for st, n in dict(info.get("by_source") or {}).items():
                source_event_counts[str(st)] = source_event_counts.get(str(st), 0) + int(n)
            if bool(info.get("has_validated_tier")):
                validated_edge_keys[ek] = True
            positive_count = int(info.get("validated_positive_count") or 0)
            negative_count = int(info.get("validated_negative_count") or 0)
            if positive_count:
                validated_positive_counts[ek] = validated_positive_counts.get(ek, 0) + positive_count
            if negative_count:
                validated_negative_counts[ek] = validated_negative_counts.get(ek, 0) + negative_count

    bead_bonus = _project_bead_bonus(edge_bonus, cap_neg, cap_pos)
    strengthened = sum(1 for v in edge_bonus.values() if v > 0)
    weakened = sum(1 for v in edge_bonus.values() if v < 0)
    validated_count_keys = sorted(set(validated_positive_counts) | set(validated_negative_counts))

    return {
        "schema": "core_memory.myelination_manifest.v2",
        "enabled": True,
        "bonus_by_edge_key": edge_bonus,
        "bonus_by_bead_id": bead_bonus,
        "has_validated_tier_by_edge_key": {ek: True for ek in sorted(validated_edge_keys)},
        "validated_reward_counts_by_edge_key": {
            ek: {
                "positive": int(validated_positive_counts.get(ek, 0)),
                "negative": int(validated_negative_counts.get(ek, 0)),
            }
            for ek in validated_count_keys
        },
        "stats": {
            "events": len(rows),
            "edges": len(edge_bonus),
            "beads": len(bead_bonus),
            "strengthened": strengthened,
            "weakened": weakened,
        },
        "source_event_counts": source_event_counts,
        "config": {
            "since": since_v,
            "limit": max(1, limit_v),
            "min_hits": min_hits,
            "pos_cap": cap_pos,
            "neg_cap": cap_neg,
            "reward_events": rewards_on,
        },
    }


def apply_contradiction_decay(root: str | Path, bonus_by_bead_id: dict[str, float]) -> dict[str, float]:
    """Reduce bonuses for beads that carry conflicting claims.

    Called from the myelination-update job after computing the bonus map.
    For each bead whose claims are in conflict status, the bonus is reduced
    by neg_cap and clamped to [-neg_cap, pos_cap]. Modifies bonus_by_bead_id
    in place and returns it.
    """
    cap_neg = max(0.0, _float_env("CORE_MEMORY_MYELINATION_NEG_CAP", 0.08))
    cap_pos = max(0.0, _float_env("CORE_MEMORY_MYELINATION_POS_CAP", 0.12))
    if cap_neg < 1e-9:
        return bonus_by_bead_id

    try:
        from core_memory.claim.resolver import resolve_all_current_state
        state = resolve_all_current_state(str(root))
        slots = dict(state.get("slots") or {})
    except Exception:
        return bonus_by_bead_id

    for slot_state in slots.values():
        if str(slot_state.get("status") or "") != "conflict":
            continue
        for conflict_claim in (slot_state.get("conflicts") or []):
            if not isinstance(conflict_claim, dict):
                continue
            bead_id = str(conflict_claim.get("source_bead_id") or "").strip()
            if not bead_id:
                continue
            current = float(bonus_by_bead_id.get(bead_id) or 0.0)
            decayed = round(_clamp(current - cap_neg, -cap_neg, cap_pos), 6)
            bonus_by_bead_id[bead_id] = decayed

    return bonus_by_bead_id


__all__ = [
    "MYELINATION_MANIFEST_SCHEMA",
    "myelination_enabled",
    "compute_myelination_bonus_map",
    "apply_contradiction_decay",
    "read_myelination_manifest",
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
