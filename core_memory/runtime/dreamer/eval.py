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


def _load_beads_index(root: Path) -> dict[str, dict[str, Any]]:
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
                name = str(s.get("name") or "").strip()
                if name:
                    out.add(name)
    return out


def _is_policy_reuse_candidate(candidate: dict[str, Any]) -> bool:
    ht = str(candidate.get("hypothesis_type") or "").strip().lower()
    if ht in {"retrieval_value_candidate", "entity_merge_candidate"}:
        return False
    rel = str(candidate.get("relationship") or "").strip().lower()
    if rel in {"transferable_lesson", "generalizes", "structural_symmetry"}:
        return True
    names = _signal_names(candidate)
    return bool({"transferability_cross_scope", "decision_outcome_lesson_shape"}.intersection(names))


def _is_repeated_mistake_candidate(candidate: dict[str, Any]) -> bool:
    hyp = str(candidate.get("hypothesis_type") or "").strip().lower()
    if hyp == "contradiction_candidate":
        return True
    return "repeated_incident" in _signal_names(candidate)


def _is_cross_session(root_beads: dict[str, dict[str, Any]], candidate: dict[str, Any]) -> bool:
    src = str(candidate.get("source_bead_id") or "")
    tgt = str(candidate.get("target_bead_id") or "")
    if not src or not tgt:
        return False
    s1 = str((root_beads.get(src) or {}).get("session_id") or "")
    s2 = str((root_beads.get(tgt) or {}).get("session_id") or "")
    return bool(s1 and s2 and s1 != s2)


def _safe_rate(numer: int, denom: int) -> float:
    return float(numer) / float(denom) if denom > 0 else 0.0


def dreamer_eval_report(root: str | Path, *, since: str = "30d") -> dict[str, Any]:
    root_p = Path(root)
    candidates = _load_candidates(root_p)
    beads = _load_beads_index(root_p)

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

    decided = [c for c in scoped if str(c.get("status") or "").lower() in {"accepted", "rejected"}]
    accepted = [c for c in scoped if str(c.get("status") or "").lower() == "accepted"]
    rejected = [c for c in scoped if str(c.get("status") or "").lower() == "rejected"]

    applied = [
        c
        for c in accepted
        if isinstance(c.get("decision"), dict)
        and (
            str((c.get("decision") or {}).get("applied_association_id") or "").strip()
            or str((c.get("decision") or {}).get("applied_turn_id") or "").strip()
        )
    ]

    repeated_candidates = [c for c in scoped if _is_repeated_mistake_candidate(c)]
    repeated_accepted = [c for c in repeated_candidates if str(c.get("status") or "").lower() == "accepted"]

    transfer_candidates = [
        c
        for c in scoped
        if _is_cross_session(beads, c)
        and (
            str(c.get("hypothesis_type") or "").lower() in {"transferable_lesson_candidate", "precedent_candidate", "abstraction_candidate"}
            or _is_policy_reuse_candidate(c)
        )
    ]
    transfer_accepted = [c for c in transfer_candidates if str(c.get("status") or "").lower() == "accepted"]

    policy_candidates = [c for c in scoped if _is_policy_reuse_candidate(c)]
    policy_accepted = [c for c in policy_candidates if str(c.get("status") or "").lower() == "accepted"]

    def accepted_used_downstream(c: dict[str, Any]) -> bool:
        src = str(c.get("source_bead_id") or "")
        tgt = str(c.get("target_bead_id") or "")
        r1 = int((beads.get(src) or {}).get("recall_count") or 0)
        r2 = int((beads.get(tgt) or {}).get("recall_count") or 0)
        return (r1 + r2) > 0

    accepted_with_downstream_use = [c for c in accepted if accepted_used_downstream(c)]
    policy_accepted_with_use = [c for c in policy_accepted if accepted_used_downstream(c)]

    accepted_rate = _safe_rate(len(accepted), len(decided))
    policy_accept_rate = _safe_rate(len(policy_accepted), len(policy_candidates))

    report = {
        "schema": "core_memory.dreamer_eval.v1",
        "root": str(root_p),
        "since": since,
        "counts": {
            "total_candidates": len(scoped),
            "decided": len(decided),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "accepted_applied": len(applied),
        },
        "metrics": {
            "accepted_candidate_rate": _safe_rate(len(accepted), len(decided)),
            "repeated_mistake_reduction_proxy": _safe_rate(len(repeated_accepted), len(repeated_candidates)),
            "cross_session_transfer_success_rate": _safe_rate(len(transfer_accepted), len(transfer_candidates)),
            "downstream_retrieval_use_rate_of_accepted_outputs": _safe_rate(
                len(accepted_with_downstream_use),
                len(accepted),
            ),
            "policy_reuse_lift_proxy": policy_accept_rate - accepted_rate,
            "policy_reuse_accept_rate": policy_accept_rate,
            "policy_reuse_downstream_use_rate": _safe_rate(len(policy_accepted_with_use), len(policy_accepted)),
        },
        "diagnostics": {
            "repeated_candidates": len(repeated_candidates),
            "cross_session_transfer_candidates": len(transfer_candidates),
            "policy_reuse_candidates": len(policy_candidates),
            "accepted_with_downstream_use": len(accepted_with_downstream_use),
        },
    }
    return report


__all__ = ["dreamer_eval_report"]
