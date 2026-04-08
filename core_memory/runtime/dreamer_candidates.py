from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any



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
        from core_memory.runtime.engine import process_turn_finalized
        from core_memory.schema.normalization import normalize_relation_type, relation_kind
        from core_memory.policy.association_inference_v21 import CANONICAL_INFERENCE_RELATIONSHIPS

        src = str(target.get("source_bead_id") or "")
        tgt = str(target.get("target_bead_id") or "")
        rel_raw = str(target.get("relationship") or "associated_with")
        rel_norm = normalize_relation_type(rel_raw)
        if rel_norm == "superseded_by":
            rel_apply = "supersedes"
        elif rel_norm in CANONICAL_INFERENCE_RELATIONSHIPS:
            rel_apply = rel_norm
        elif relation_kind(rel_norm) == "canonical":
            # Dreamer canonical relations can be richer than strict inference set;
            # downgrade to strict-safe edge for turn-time association append path.
            rel_apply = "supports"
        else:
            rel_apply = "supports"
        if src and tgt:
            idx_path = Path(root) / ".beads" / "index.json"
            before_ids: set[str] = set()
            session_id = str(((target.get("run_metadata") or {}) if isinstance(target.get("run_metadata"), dict) else {}).get("session_id") or "").strip()
            if idx_path.exists():
                try:
                    idx = json.loads(idx_path.read_text(encoding="utf-8"))
                    before_ids = {
                        str(a.get("id") or "")
                        for a in (idx.get("associations") or [])
                        if isinstance(a, dict) and str(a.get("id") or "")
                    }
                    if not session_id:
                        src_row = (idx.get("beads") or {}).get(src) if isinstance(idx.get("beads"), dict) else None
                        session_id = str((src_row or {}).get("session_id") or "").strip()
                except Exception:
                    before_ids = set()

            session_id = session_id or "dreamer-review"
            turn_id = f"dreamer-apply-{cid}"
            rationale = str(target.get("rationale") or "dreamer_candidate_accept")
            out = process_turn_finalized(
                root=str(root),
                session_id=session_id,
                turn_id=turn_id,
                user_query=f"Dreamer reviewer accepted candidate {cid}; apply reviewed association.",
                assistant_final=f"Apply reviewed association: {src} {rel_apply} {tgt}. Rationale: {rationale}",
                metadata={
                    "crawler_updates": {
                        "associations": [
                            {
                                "source_bead_id": src,
                                "target_bead_id": tgt,
                                "relationship": rel_apply,
                                "relationship_raw": rel_raw,
                                "confidence": float(target.get("confidence") or 0.7),
                                "reason_text": rationale,
                                "rationale": rationale,
                                "provenance": "reviewer_decision",
                            }
                        ]
                    }
                },
            )
            assoc_id = ""
            if idx_path.exists():
                try:
                    idx_after = json.loads(idx_path.read_text(encoding="utf-8"))
                    new_rows = [
                        a
                        for a in (idx_after.get("associations") or [])
                        if isinstance(a, dict)
                        and str(a.get("id") or "")
                        and str(a.get("id") or "") not in before_ids
                        and str(a.get("source_bead") or "") == src
                        and str(a.get("target_bead") or "") == tgt
                        and str(a.get("relationship") or "") == rel_apply
                    ]
                    if new_rows:
                        new_rows = sorted(new_rows, key=lambda a: str(a.get("created_at") or ""))
                        assoc_id = str((new_rows[-1] or {}).get("id") or "")
                except Exception:
                    assoc_id = ""

            auto_apply = (((out.get("crawler_handoff") or {}).get("auto_apply") or {}) if isinstance(out, dict) else {})
            turn_merge = (((out.get("crawler_handoff") or {}).get("turn_merge") or {}) if isinstance(out, dict) else {})
            appended = int(auto_apply.get("associations_appended") or 0) + int(turn_merge.get("associations_appended") or 0)
            ok_apply = bool(out.get("ok"))

            applied = {
                "ok": ok_apply,
                "association_id": assoc_id or None,
                "canonical_entry": "process_turn_finalized",
                "turn_id": turn_id,
                "session_id": session_id,
                "relationship": rel_apply,
                "relationship_raw": rel_raw,
                "appended_count": appended,
                "application_mode": "association_append" if (bool(assoc_id) or appended > 0) else "canonical_review_record_only",
                "engine": out,
            }
            if assoc_id:
                target["decision"]["applied_association_id"] = assoc_id
            target["decision"]["applied_turn_id"] = turn_id
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
