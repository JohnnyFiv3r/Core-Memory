from __future__ import annotations

import os
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.soul.store import soul_history
from core_memory.soul.summary import build_soul_summary

SELF_MODEL_DRIFT_SCHEMA = "self_model_drift_meter.v1"
_NEGATION_RE = re.compile(r"\b(no|not|never|without|cannot|can't|won't|reject|avoid|oppose)\b", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _since_cutoff(since: str) -> datetime | None:
    m = re.fullmatch(r"(\d+)\s*([dh])", str(since or "").strip().lower())
    if not m:
        return None
    n = int(m.group(1))
    delta = n * (24 if m.group(2) == "d" else 1)
    from datetime import timedelta

    return datetime.now(timezone.utc) - timedelta(hours=delta)


def _tokens(text: Any) -> set[str]:
    return {t.lower() for t in re.findall(r"[A-Za-z0-9_]+", str(text or "")) if len(t) > 2}


def _evidence_bead_ids(revision: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for ev in revision.get("evidence") or []:
        if not isinstance(ev, dict):
            continue
        if str(ev.get("type") or "").lower() == "bead" and str(ev.get("id") or "").strip():
            out.append(str(ev.get("id")).strip())
    return out


def _index_beads(root: str | Path) -> dict[str, dict[str, Any]]:
    p = Path(root) / ".beads" / "index.json"
    if not p.exists():
        return {}
    try:
        idx = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {str(k): dict(v) for k, v in dict(idx.get("beads") or {}).items() if isinstance(v, dict)}


def _qualifying_evidence_bead_ids(
    root: str | Path,
    evidence_ids: list[str],
    previous: dict[str, Any] | None,
) -> list[str]:
    beads = _index_beads(root)
    previous_dt = _parse_iso(previous.get("created_at")) if previous else None
    out: list[str] = []
    for bead_id in evidence_ids:
        bead = beads.get(str(bead_id), {})
        bead_type = str(bead.get("type") or "").lower()
        if bead_type not in {"decision", "outcome"}:
            continue
        if str(bead.get("status") or "active").lower() in {"superseded", "archived", "deleted"}:
            continue
        bead_dt = _parse_iso(bead.get("created_at"))
        if previous_dt is not None and bead_dt is not None and bead_dt <= previous_dt:
            continue
        out.append(str(bead_id))
    return out


def _meaningfully_changed(previous: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if not previous:
        return False
    old = _tokens(previous.get("content"))
    new = _tokens(current.get("content"))
    if old == new:
        return False
    if _NEGATION_RE.search(str(previous.get("content") or "")) != _NEGATION_RE.search(str(current.get("content") or "")):
        return True
    if not old or not new:
        return str(previous.get("content") or "").strip() != str(current.get("content") or "").strip()
    overlap = len(old & new) / float(max(1, len(old | new)))
    return overlap < 0.45


def _accepted_divergence_for_key(root: str | Path, key: str) -> bool:
    p = Path(root) / ".beads" / "events" / "dreamer-candidates.json"
    if not p.exists():
        return False
    try:
        rows = [row for row in json.loads(p.read_text(encoding="utf-8")) if isinstance(row, dict)]
    except Exception:
        return False
    key_l = key.strip().lower()
    for row in rows:
        if str(row.get("hypothesis_type") or "") != "identity_divergence_candidate":
            continue
        if str(row.get("status") or "").lower() not in {"accepted", "applied", "approved"}:
            continue
        candidate_key = str(row.get("identity_entry_key") or row.get("entry_key") or "").strip().lower()
        if candidate_key == key_l:
            return True
    return False


def compute_self_model_drift(
    root: str | Path,
    *,
    since: str | None = None,
    subject: str = "self",
) -> dict[str, Any]:
    window = str(since or os.getenv("CORE_MEMORY_QUALITY_METER_SINCE", "30d"))
    cutoff = _since_cutoff(window)
    history = soul_history(root, subject=subject, limit=1_000_000)
    revisions = [
        dict(row)
        for row in (history.get("revisions") or [])
        if isinstance(row, dict)
        and row.get("target_file") == "IDENTITY.md"
        and row.get("status") == "applied"
        and row.get("op") != "remove"
        and str(row.get("epistemic_status") or "").lower() == "endorsed"
    ]

    prior_by_key: dict[str, dict[str, Any]] = {}
    flagged: list[dict[str, Any]] = []
    seen_in_window = 0
    for revision in revisions:
        key = str(revision.get("entry_key") or "").strip()
        if not key:
            continue
        created = _parse_iso(revision.get("created_at"))
        in_window = cutoff is None or created is None or created >= cutoff
        previous = prior_by_key.get(key)
        prior_by_key[key] = revision
        if not in_window:
            continue
        seen_in_window += 1
        evidence_ids = _evidence_bead_ids(revision)
        qualifying_evidence_ids = _qualifying_evidence_bead_ids(root, evidence_ids, previous)
        if not qualifying_evidence_ids:
            flagged.append(
                {
                    "revision_id": str(revision.get("id") or ""),
                    "entry_key": key,
                    "flag": "ungrounded_update",
                    "changed_at": str(revision.get("created_at") or ""),
                    "evidence_bead_ids": evidence_ids,
                    "detail": "Endorsed identity entry modified with no qualifying behavior bead evidence.",
                }
            )
        if _meaningfully_changed(previous, revision) and not _accepted_divergence_for_key(root, key):
            flagged.append(
                {
                    "revision_id": str(revision.get("id") or ""),
                    "entry_key": key,
                    "flag": "contradiction_without_observation",
                    "changed_at": str(revision.get("created_at") or ""),
                    "evidence_bead_ids": evidence_ids,
                    "detail": "Endorsed identity entry changed without an accepted identity-divergence observation.",
                }
            )

    ungrounded = sum(1 for row in flagged if row["flag"] == "ungrounded_update")
    contradictions = sum(1 for row in flagged if row["flag"] == "contradiction_without_observation")
    drift_score = int(ungrounded + (3 * contradictions))
    if seen_in_window == 0:
        status = "insufficient_data"
    elif drift_score == 0:
        status = "healthy"
    elif drift_score >= 5:
        status = "high_drift"
    else:
        status = "drifting"

    try:
        divergence = dict(build_soul_summary(root, subject=subject).get("observed_endorsed_divergence") or {})
        divergence_index = float(divergence.get("divergence_index") or 0.0)
    except Exception:
        divergence_index = None

    return {
        "schema": SELF_MODEL_DRIFT_SCHEMA,
        "window": window,
        "generated_at": _now(),
        "status": status,
        "drift_score": drift_score,
        "revision_count": seen_in_window,
        "flagged_revision_count": len(flagged),
        "ungrounded_update_count": ungrounded,
        "contradiction_without_observation_count": contradictions,
        "divergence_index": divergence_index,
        "flagged_revisions": flagged,
        "evidence_requirements": [
            "active_decision_or_outcome_bead",
            "evidence_bead_created_after_prior_identity_revision",
            "accepted_identity_divergence_observation_for_contradictions",
        ],
        "limitations": [],
    }


__all__ = ["SELF_MODEL_DRIFT_SCHEMA", "compute_self_model_drift"]
