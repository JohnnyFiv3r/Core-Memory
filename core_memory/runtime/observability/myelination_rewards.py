"""Myelination V2 — audited reward/decay events over association edges.

A reward event records that an audited decision (human approval/rejection, goal
resolution, candidate decision, claim-conflict resolution, overlay decision)
should reinforce or weaken specific traversal edges. Events are append-only and
never mutate beads, claims, or C/B/A — they fuse into the edge-bonus manifest
(see ``compute_myelination_bonus_map``).

Edge-only invariant: a reward event always targets concrete ``edge_key``s
(``source|relationship|target``). A decision with no resolvable supporting edge
emits no event — reward is never smeared across a bead and its unrelated
neighbours.
"""
from __future__ import annotations

import json
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.persistence.io_utils import append_jsonl, store_lock
from core_memory.runtime.observability.myelination import (
    _edge_key,
    myelination_enabled,
)
from core_memory.runtime.observability.retrieval_feedback import read_retrieval_feedback

REWARD_EVENT_SCHEMA = "myelination_reward_event.v1"

# Evidential relationships whose incident associations count as a bead's
# concrete supporting edges (PRD §9.3).
EVIDENTIAL_RELATIONSHIPS = {
    "supports",
    "derived_from",
    "caused_by",
    "resolves",
    "led_to",
}

_VALID_SOURCE_TYPES = {
    "retrieval_feedback",
    "human_approval",
    "human_rejection",
    "goal_resolution",
    "dreamer_candidate_decision",
    "claim_conflict_resolution",
    "overlay_decision",
}


def reward_events_enabled() -> bool:
    """Whether reward events fuse into the manifest. Defaults to the master
    myelination switch unless explicitly overridden."""
    raw = os.getenv("CORE_MEMORY_MYELINATION_REWARD_EVENTS_ENABLED")
    if raw is None:
        return myelination_enabled()
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


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


def _rewards_path(root: str | Path) -> Path:
    return Path(root) / ".beads" / "events" / "myelination-rewards.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _index_path(root: str | Path) -> Path:
    return Path(root) / ".beads" / "index.json"


def _read_index(root: str | Path) -> dict[str, Any]:
    p = _index_path(root)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def supporting_edge_keys_for_bead(root: str | Path, bead_id: str, *, include_recall_trace: bool = True) -> list[str]:
    """Resolve the concrete supporting edges for a bead (PRD §9.3).

    Union of:
      1. evidence edges — associations incident to ``bead_id`` whose relationship
         is evidential (``supports``/``derived_from``/``caused_by``/``resolves``/
         ``led_to``);
      2. recall-trace edges — traversed chain edges from retrieval-feedback rows
         that surfaced ``bead_id`` (bounded, recent).

    Edge keys are the stored direction ``source|relationship|target``.
    """
    bid = str(bead_id or "").strip()
    if not bid:
        return []

    keys: list[str] = []
    seen: set[str] = set()

    index = _read_index(root)
    for assoc in (index.get("associations") or []):
        if not isinstance(assoc, dict):
            continue
        rel = str(assoc.get("relationship") or "").strip().lower()
        if rel not in EVIDENTIAL_RELATIONSHIPS:
            continue
        src = str(assoc.get("source_bead") or "").strip()
        dst = str(assoc.get("target_bead") or "").strip()
        if bid not in (src, dst) or not src or not dst:
            continue
        ek = _edge_key(src, dst, rel)
        if ek not in seen:
            seen.add(ek)
            keys.append(ek)

    if include_recall_trace:
        try:
            rows = read_retrieval_feedback(
                root,
                since=str(os.getenv("CORE_MEMORY_MYELINATION_SINCE", "30d")),
                limit=_int_env("CORE_MEMORY_MYELINATION_REWARD_EVENT_LIMIT", 2000),
            )
        except Exception:
            rows = []
        for row in rows:
            resp = dict(row.get("response") or {})
            if bid not in [str(x) for x in (resp.get("result_bead_ids") or [])]:
                continue
            for e in (resp.get("edges") or []):
                if not isinstance(e, dict):
                    continue
                src = str(e.get("src") or "").strip()
                dst = str(e.get("dst") or "").strip()
                rel = str(e.get("rel") or "").strip()
                if not src or not dst or not rel:
                    continue
                ek = _edge_key(src, dst, rel)
                if ek not in seen:
                    seen.add(ek)
                    keys.append(ek)

    return keys


def emit_myelination_reward_event(
    root: str | Path,
    *,
    source_type: str,
    polarity: str,
    edge_keys: list[str],
    strength: float | None = None,
    source_event_id: str = "",
    supporting_bead_ids: list[str] | None = None,
    supporting_claim_ids: list[str] | None = None,
    supporting_candidate_ids: list[str] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """Append a ``myelination_reward_event.v1`` over concrete edges.

    Returns ``{"ok": False, "skipped": "<why>"}`` without writing when there are
    no concrete edge keys (the edge-only guardrail) — the caller should record a
    governance event separately if it needs an audit trail.
    """
    eks = [str(k).strip() for k in (edge_keys or []) if str(k).strip()]
    if not eks:
        return {"ok": False, "skipped": "no_concrete_edges"}

    pol = str(polarity or "").strip().lower()
    if pol not in {"positive", "negative"}:
        return {"ok": False, "skipped": "bad_polarity"}

    st = str(source_type or "").strip().lower()
    if st not in _VALID_SOURCE_TYPES:
        return {"ok": False, "skipped": "bad_source_type"}

    default_strength = _float_env("CORE_MEMORY_MYELINATION_REWARD_STRENGTH", 0.04)
    s = abs(float(strength)) if strength is not None else default_strength

    row = {
        "schema": REWARD_EVENT_SCHEMA,
        "id": f"mr-{uuid.uuid4().hex[:12]}",
        "created_at": _now(),
        "source_type": st,
        "source_event_id": str(source_event_id or ""),
        "polarity": pol,
        "strength": round(float(s), 6),
        "edge_keys": eks,
        "supporting_bead_ids": [str(x) for x in (supporting_bead_ids or []) if str(x)],
        "supporting_claim_ids": [str(x) for x in (supporting_claim_ids or []) if str(x)],
        "supporting_candidate_ids": [str(x) for x in (supporting_candidate_ids or []) if str(x)],
        "reason": str(reason or ""),
        "guardrails": {
            "requires_concrete_edges": True,
            "mutates_beads": False,
            "mutates_claims": False,
            "mutates_overlays": False,
            "mutates_soul": False,
        },
    }

    path = _rewards_path(root)
    with store_lock(Path(root)):
        append_jsonl(path, row)
    return {"ok": True, "event_id": row["id"], "edge_count": len(eks)}


def reward_for_bead_decision(
    root: str | Path,
    *,
    bead_id: str,
    polarity: str,
    source_type: str,
    source_event_id: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Convenience: resolve a bead's concrete supporting edges and emit one
    reward event. No-op when myelination is disabled or no edges resolve."""
    if not myelination_enabled():
        return {"ok": False, "skipped": "disabled"}
    eks = supporting_edge_keys_for_bead(root, bead_id)
    if not eks:
        return {"ok": False, "skipped": "no_concrete_edges"}
    return emit_myelination_reward_event(
        root,
        source_type=source_type,
        polarity=polarity,
        edge_keys=eks,
        source_event_id=source_event_id,
        supporting_bead_ids=[str(bead_id)],
        reason=reason,
    )


def read_reward_events(root: str | Path, *, since: str = "30d", limit: int = 2000) -> list[dict[str, Any]]:
    p = _rewards_path(root)
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except Exception:
                continue
            if isinstance(row, dict) and row.get("schema") == REWARD_EVENT_SCHEMA:
                rows.append(row)
    return rows[-max(1, int(limit)):]


def reward_bonus_by_edge_key(root: str | Path, *, since: str = "30d", limit: int = 2000) -> dict[str, dict[str, Any]]:
    """Aggregate signed reward bonus and event count per edge key:
    ``{edge_key: {"bonus": signed_sum, "count": n, "by_source": {...}}}``."""
    out: dict[str, dict[str, Any]] = {}
    for row in read_reward_events(root, since=since, limit=limit):
        sign = 1.0 if str(row.get("polarity")) == "positive" else -1.0
        strength = float(row.get("strength") or 0.0)
        st = str(row.get("source_type") or "")
        for ek in (row.get("edge_keys") or []):
            k = str(ek).strip()
            if not k:
                continue
            slot = out.setdefault(k, {"bonus": 0.0, "count": 0, "by_source": Counter()})
            slot["bonus"] += sign * strength
            slot["count"] += 1
            slot["by_source"][st] += 1
    for slot in out.values():
        slot["bonus"] = round(float(slot["bonus"]), 6)
        slot["by_source"] = dict(slot["by_source"])
    return out


__all__ = [
    "REWARD_EVENT_SCHEMA",
    "EVIDENTIAL_RELATIONSHIPS",
    "reward_events_enabled",
    "supporting_edge_keys_for_bead",
    "emit_myelination_reward_event",
    "reward_for_bead_decision",
    "read_reward_events",
    "reward_bonus_by_edge_key",
]
