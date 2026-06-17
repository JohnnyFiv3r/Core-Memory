from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core_memory.persistence.io_utils import append_jsonl, store_lock

DREAMER_EVAL_LABEL_SCHEMA = "core_memory.dreamer_eval_label.v1"
_LABELS = {"true_positive", "false_positive", "unclear"}


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


def _labels_path(root: Path) -> Path:
    return root / ".beads" / "events" / "dreamer-eval-labels.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _candidate_type_by_id(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in _load_candidates(root):
        cid = str(c.get("id") or "").strip()
        if cid:
            out[cid] = str(c.get("hypothesis_type") or "").strip()
    return out


def append_dreamer_eval_label(
    root: str | Path,
    *,
    candidate_id: str,
    label: str,
    actionable: bool,
    reviewer: str = "",
    notes: str = "",
    hypothesis_type: str = "",
) -> dict[str, Any]:
    """Append a human review label for Dreamer precision/actionability eval."""
    root_p = Path(root)
    cid = str(candidate_id or "").strip()
    if not cid:
        return {"ok": False, "error": "missing_candidate_id"}
    label_n = str(label or "").strip().lower()
    if label_n not in _LABELS:
        return {"ok": False, "error": "invalid_label", "allowed": sorted(_LABELS)}
    htype = str(hypothesis_type or "").strip()
    if not htype:
        htype = _candidate_type_by_id(root_p).get(cid, "")
    row = {
        "schema": DREAMER_EVAL_LABEL_SCHEMA,
        "id": f"del-{uuid.uuid4().hex[:12]}",
        "created_at": _now(),
        "candidate_id": cid,
        "hypothesis_type": htype,
        "label": label_n,
        "actionable": bool(actionable),
        "reviewer": str(reviewer or ""),
        "notes": str(notes or ""),
    }
    with store_lock(root_p):
        append_jsonl(_labels_path(root_p), row)
    return {"ok": True, "label_id": row["id"], "candidate_id": cid, "label": label_n}


def read_dreamer_eval_labels(root: str | Path) -> list[dict[str, Any]]:
    p = _labels_path(Path(root))
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
            if isinstance(row, dict) and row.get("schema") == DREAMER_EVAL_LABEL_SCHEMA:
                rows.append(row)
    return rows


def _latest_labels_by_candidate(root: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in read_dreamer_eval_labels(root):
        cid = str(row.get("candidate_id") or "").strip()
        if cid:
            out[cid] = row
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
    rel = str(candidate.get("relationship_signal") or candidate.get("relationship_raw") or candidate.get("relationship") or "").strip().lower()
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


def _label_summary(scoped: list[dict[str, Any]], labels_by_candidate: dict[str, dict[str, Any]], *, sample_limit: int) -> dict[str, Any]:
    counts_by_type: dict[str, dict[str, int]] = {}
    labeled_candidate_ids: set[str] = set()

    for c in scoped:
        cid = str(c.get("id") or "").strip()
        if not cid or cid not in labels_by_candidate:
            continue
        label = labels_by_candidate[cid]
        label_value = str(label.get("label") or "").strip().lower()
        if label_value not in _LABELS:
            continue
        htype = str(label.get("hypothesis_type") or c.get("hypothesis_type") or "unknown").strip() or "unknown"
        slot = counts_by_type.setdefault(htype, {
            "total": 0,
            "true_positive": 0,
            "false_positive": 0,
            "unclear": 0,
            "actionable": 0,
        })
        slot["total"] += 1
        slot[label_value] += 1
        if bool(label.get("actionable")):
            slot["actionable"] += 1
        labeled_candidate_ids.add(cid)

    precision_by_type: dict[str, float] = {}
    actionability_rate_by_type: dict[str, float] = {}
    for htype, counts in counts_by_type.items():
        precision_by_type[htype] = _safe_rate(
            int(counts.get("true_positive") or 0),
            int(counts.get("true_positive") or 0) + int(counts.get("false_positive") or 0),
        )
        actionability_rate_by_type[htype] = _safe_rate(int(counts.get("actionable") or 0), int(counts.get("total") or 0))

    unlabeled = [c for c in scoped if str(c.get("id") or "").strip() not in labeled_candidate_ids]
    unlabeled.sort(key=lambda c: (str(c.get("created_at") or ""), str(c.get("id") or "")))
    samples = [
        {
            "candidate_id": str(c.get("id") or ""),
            "hypothesis_type": str(c.get("hypothesis_type") or ""),
            "relationship": str(c.get("relationship") or ""),
            "relationship_signal": str(c.get("relationship_signal") or c.get("relationship_raw") or ""),
            "source_bead_id": str(c.get("source_bead_id") or ""),
            "target_bead_id": str(c.get("target_bead_id") or ""),
            "status": str(c.get("status") or ""),
        }
        for c in unlabeled[: max(0, int(sample_limit))]
    ]
    return {
        "counts_by_type": counts_by_type,
        "precision_by_type": precision_by_type,
        "actionability_rate_by_type": actionability_rate_by_type,
        "unlabeled_review_samples": samples,
    }


def dreamer_eval_report(root: str | Path, *, since: str = "30d", sample_limit: int = 25) -> dict[str, Any]:
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

    theme_candidates = [c for c in scoped if str(c.get("hypothesis_type") or "").lower() == "proposed_theme_candidate"]
    theme_decided = [c for c in theme_candidates if str(c.get("status") or "").lower() in {"accepted", "rejected"}]
    theme_accepted = [c for c in theme_candidates if str(c.get("status") or "").lower() == "accepted"]

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
            "theme_candidates": len(theme_candidates),
            "theme_decided": len(theme_decided),
            "theme_accepted": len(theme_accepted),
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
            "theme_acceptance_rate": _safe_rate(len(theme_accepted), len(theme_decided)),
        },
        "diagnostics": {
            "repeated_candidates": len(repeated_candidates),
            "cross_session_transfer_candidates": len(transfer_candidates),
            "policy_reuse_candidates": len(policy_candidates),
            "accepted_with_downstream_use": len(accepted_with_downstream_use),
        },
        "human_labels": _label_summary(scoped, _latest_labels_by_candidate(root_p), sample_limit=sample_limit),
    }
    return report


__all__ = [
    "DREAMER_EVAL_LABEL_SCHEMA",
    "append_dreamer_eval_label",
    "dreamer_eval_report",
    "read_dreamer_eval_labels",
]
