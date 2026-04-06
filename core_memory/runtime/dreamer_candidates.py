from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.persistence.store import MemoryStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _events_dir(root: str | Path) -> Path:
    p = Path(root) / ".beads" / "events"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _candidates_path(root: str | Path) -> Path:
    return _events_dir(root) / "dreamer-candidates.json"


def _read_candidates(root: str | Path) -> list[dict[str, Any]]:
    p = _candidates_path(root)
    if not p.exists():
        return []
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
    except Exception:
        pass
    return []


def _write_candidates(root: str | Path, rows: list[dict[str, Any]]) -> None:
    p = _candidates_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _hypothesis_type(relationship: str) -> str:
    rel = str(relationship or "").strip().lower()
    if rel == "transferable_lesson":
        return "transferable_lesson_candidate"
    if rel == "contradicts":
        return "contradiction_candidate"
    if rel in {"generalizes", "reveals_bias"}:
        return "abstraction_candidate"
    if rel == "structural_symmetry":
        return "precedent_candidate"
    return "association_candidate"


def _expected_decision_impact(source_title: str, target_title: str, relationship: str) -> str:
    rel = str(relationship or "related")
    return f"Review {rel} between '{source_title or 'source'}' and '{target_title or 'target'}' before similar future decisions."


def enqueue_dreamer_candidates(
    *,
    root: str | Path,
    associations: list[dict[str, Any]],
    run_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = _read_candidates(root)
    now = _now()
    run_meta = dict(run_metadata or {})

    added = 0
    for a in list(associations or []):
        if not isinstance(a, dict):
            continue
        src = str(a.get("source") or "").strip()
        tgt = str(a.get("target") or "").strip()
        if not src or not tgt:
            continue
        rel = str(a.get("relationship") or "similar_pattern").strip() or "similar_pattern"
        row = {
            "id": f"dc-{uuid.uuid4().hex[:12]}",
            "created_at": now,
            "status": "pending",
            "hypothesis_type": _hypothesis_type(rel),
            "source_bead_id": src,
            "target_bead_id": tgt,
            "relationship": rel,
            "novelty": float(a.get("novelty") or 0.0),
            "grounding": float(a.get("grounding") or 0.0),
            "confidence": float(a.get("confidence") or 0.0),
            "rationale": str(a.get("insight") or a.get("rationale") or f"Dreamer suggested {rel} based on structural similarity."),
            "expected_decision_impact": str(
                a.get("decision_impact")
                or a.get("expected_decision_impact")
                or _expected_decision_impact(
                    str(a.get("source_title") or ""),
                    str(a.get("target_title") or ""),
                    rel,
                )
            ),
            "run_metadata": {
                "run_id": str(run_meta.get("run_id") or ""),
                "mode": str(run_meta.get("mode") or "suggest"),
                "source": str(run_meta.get("source") or "side_effect_queue"),
                "session_id": str(run_meta.get("session_id") or ""),
                "flush_tx_id": str(run_meta.get("flush_tx_id") or ""),
                "novel_only": bool(run_meta.get("novel_only", True)),
                "seen_window_runs": int(run_meta.get("seen_window_runs") or 0),
                "max_exposure": int(run_meta.get("max_exposure") or -1),
            },
            "raw": dict(a),
        }
        rows.append(row)
        added += 1

    _write_candidates(root, rows)
    return {
        "ok": True,
        "added": added,
        "queue_depth": len(rows),
        "path": str(_candidates_path(root)),
    }


def list_dreamer_candidates(
    *,
    root: str | Path,
    status: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    rows = _read_candidates(root)
    status_n = str(status or "").strip().lower()
    if status_n:
        rows = [r for r in rows if str(r.get("status") or "").strip().lower() == status_n]
    rows = sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return {
        "ok": True,
        "count": len(rows),
        "results": rows[: max(1, int(limit))],
        "path": str(_candidates_path(root)),
    }


def decide_dreamer_candidate(
    *,
    root: str | Path,
    candidate_id: str,
    decision: str,
    reviewer: str | None = None,
    notes: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    cid = str(candidate_id or "").strip()
    decision_n = str(decision or "").strip().lower()
    if decision_n not in {"accept", "reject"}:
        return {
            "ok": False,
            "error": {"code": "invalid_decision", "decision": decision_n, "allowed": ["accept", "reject"]},
        }

    rows = _read_candidates(root)
    target: dict[str, Any] | None = None
    for r in rows:
        if str(r.get("id") or "") == cid:
            target = r
            break
    if target is None:
        return {"ok": False, "error": {"code": "candidate_not_found", "candidate_id": cid}}

    target["status"] = "accepted" if decision_n == "accept" else "rejected"
    target["decision"] = {
        "decision": decision_n,
        "reviewer": str(reviewer or ""),
        "notes": str(notes or ""),
        "decided_at": _now(),
    }

    applied = None
    if decision_n == "accept" and apply:
        src = str(target.get("source_bead_id") or "")
        tgt = str(target.get("target_bead_id") or "")
        rel = str(target.get("relationship") or "associated_with")
        if src and tgt:
            store = MemoryStore(root=str(root))
            assoc_id = store.link(
                source_id=src,
                target_id=tgt,
                relationship=rel,
                explanation=str(target.get("rationale") or "dreamer_candidate_accept"),
                confidence=float(target.get("confidence") or 0.7),
            )
            applied = {
                "ok": True,
                "association_id": assoc_id,
            }
            target["decision"]["applied_association_id"] = assoc_id
        else:
            applied = {"ok": False, "error": "missing_source_or_target"}

    _write_candidates(root, rows)
    return {
        "ok": True,
        "candidate_id": cid,
        "status": target.get("status"),
        "applied": applied,
        "path": str(_candidates_path(root)),
    }


__all__ = [
    "enqueue_dreamer_candidates",
    "list_dreamer_candidates",
    "decide_dreamer_candidate",
]
