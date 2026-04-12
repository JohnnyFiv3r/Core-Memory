from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
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


def _proposal_family(hypothesis_type: str) -> str:
    ht = str(hypothesis_type or "").strip().lower()
    if ht == "contradiction_candidate":
        return "contradiction"
    if ht == "entity_merge_candidate":
        return "entity_identity"
    if ht == "retrieval_value_candidate":
        return "retrieval_value"
    return "association"


def _benchmark_tags_for_hypothesis(hypothesis_type: str) -> list[str]:
    ht = str(hypothesis_type or "").strip().lower()
    if ht == "contradiction_candidate":
        return ["contradiction_update", "current_state_factual"]
    if ht == "entity_merge_candidate":
        return ["entity_coreference"]
    if ht == "retrieval_value_candidate":
        return ["causal_mechanism", "current_state_factual", "entity_coreference"]
    if ht in {"transferable_lesson_candidate", "abstraction_candidate", "precedent_candidate"}:
        return ["causal_mechanism"]
    return ["causal_mechanism", "current_state_factual"]


def _normalize_entity_alias(value: str) -> str:
    import re

    s = str(value or "").strip().lower()
    s = re.sub(r"[\s\-_/]+", " ", s)
    s = re.sub(r"[^a-z0-9\s]+", "", s)
    s = re.sub(r"\b(inc|incorporated|corp|corporation|llc|ltd|limited|co|company)\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace(" ", "")


def _read_index(root: str | Path) -> dict[str, Any]:
    p = Path(root) / ".beads" / "index.json"
    if not p.exists():
        return {}
    try:
        out = json.loads(p.read_text(encoding="utf-8"))
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


def _entity_similarity(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, list[str]]:
    a_label = _normalize_entity_alias(str(a.get("normalized_label") or a.get("label") or ""))
    b_label = _normalize_entity_alias(str(b.get("normalized_label") or b.get("label") or ""))
    a_aliases = {_normalize_entity_alias(str(x)) for x in (a.get("aliases") or []) if str(x).strip()}
    b_aliases = {_normalize_entity_alias(str(x)) for x in (b.get("aliases") or []) if str(x).strip()}
    a_aliases = {x for x in a_aliases if x}
    b_aliases = {x for x in b_aliases if x}

    reasons: list[str] = []
    overlap = a_aliases.intersection(b_aliases)
    overlap_score = min(1.0, len(overlap) / max(1.0, len(a_aliases.union(b_aliases)))) if (a_aliases or b_aliases) else 0.0
    if overlap:
        reasons.append("alias_overlap")

    seq = 0.0
    if a_label and b_label:
        seq = SequenceMatcher(None, a_label, b_label).ratio()
        if seq >= 0.90:
            reasons.append("label_similarity_high")

    containment = 1.0 if (a_label and b_label and (a_label in b_label or b_label in a_label)) else 0.0
    if containment > 0.0:
        reasons.append("label_containment")

    score = (0.45 * overlap_score) + (0.40 * seq) + (0.15 * containment)
    return (float(score), reasons)


def _candidate_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            str(row.get("hypothesis_type") or ""),
            str(row.get("source_bead_id") or ""),
            str(row.get("target_bead_id") or ""),
            str(row.get("relationship") or ""),
            str(row.get("source_entity_id") or ""),
            str(row.get("target_entity_id") or ""),
        ]
    )


def _make_candidate_row(*, now: str, run_meta: dict[str, Any], association: dict[str, Any], hypothesis_type: str, rationale: str, expected_decision_impact: str, extras: dict[str, Any] | None = None) -> dict[str, Any]:
    src = str(association.get("source") or association.get("source_bead_id") or "").strip()
    tgt = str(association.get("target") or association.get("target_bead_id") or "").strip()
    rel = str(association.get("relationship") or "similar_pattern").strip() or "similar_pattern"

    row = {
        "id": f"dc-{uuid.uuid4().hex[:12]}",
        "created_at": now,
        "status": "pending",
        "hypothesis_type": hypothesis_type,
        "proposal_family": _proposal_family(hypothesis_type),
        "benchmark_tags": _benchmark_tags_for_hypothesis(hypothesis_type),
        "source_bead_id": src,
        "target_bead_id": tgt,
        "relationship": rel,
        "novelty": float(association.get("novelty") or 0.0),
        "grounding": float(association.get("grounding") or 0.0),
        "confidence": float(association.get("confidence") or 0.0),
        "rationale": str(rationale),
        "expected_decision_impact": str(expected_decision_impact),
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
        "raw": dict(association),
    }
    if extras:
        row.update(dict(extras))
    return row


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
    index = _read_index(root)
    bead_map = (index.get("beads") or {}) if isinstance(index, dict) else {}
    entity_map = (index.get("entities") or {}) if isinstance(index, dict) else {}

    seen_keys = {_candidate_key(r) for r in rows if isinstance(r, dict)}

    # DV2-2 retrieval feedback loop context (review-only signal input)
    try:
        from core_memory.runtime.retrieval_feedback import summarize_retrieval_feedback

        feedback_since = str(run_meta.get("feedback_since") or "30d")
        feedback_limit = int(run_meta.get("feedback_limit") or 500)
        fb = summarize_retrieval_feedback(root, since=feedback_since, limit=feedback_limit)
    except Exception:
        fb = {"counts": {"successful": 0}, "top_beads": [], "top_edges": [], "top_slots": []}

    bead_hit_map = {str(r.get("bead_id") or ""): int(r.get("hits") or 0) for r in list(fb.get("top_beads") or []) if str(r.get("bead_id") or "")}
    edge_hit_map = {
        (str(r.get("src") or ""), str(r.get("dst") or ""), str(r.get("rel") or "")): int(r.get("hits") or 0)
        for r in list(fb.get("top_edges") or [])
        if str(r.get("src") or "") and str(r.get("dst") or "") and str(r.get("rel") or "")
    }
    total_success = int(((fb.get("counts") or {}).get("successful") or 0))

    def _feedback_for_association(src: str, tgt: str, rel: str) -> dict[str, Any]:
        src_hits = int(bead_hit_map.get(src) or 0)
        tgt_hits = int(bead_hit_map.get(tgt) or 0)
        edge_hits = int(edge_hit_map.get((src, tgt, rel)) or 0)
        edge_hits += int(edge_hit_map.get((tgt, src, rel)) or 0)
        confidence = 0.0
        if total_success > 0:
            confidence = min(1.0, (0.35 * edge_hits + 0.10 * src_hits + 0.10 * tgt_hits) / float(total_success))
        return {
            "since": str(run_meta.get("feedback_since") or "30d"),
            "total_successful_retrievals": total_success,
            "source_bead_hits": src_hits,
            "target_bead_hits": tgt_hits,
            "edge_hits": edge_hits,
            "confidence": round(float(confidence), 4),
        }

    added = 0
    for a in list(associations or []):
        if not isinstance(a, dict):
            continue
        src = str(a.get("source") or "").strip()
        tgt = str(a.get("target") or "").strip()
        if not src or not tgt:
            continue
        rel = str(a.get("relationship") or "similar_pattern").strip() or "similar_pattern"
        feedback = _feedback_for_association(src, tgt, rel)

        base = _make_candidate_row(
            now=now,
            run_meta=run_meta,
            association=a,
            hypothesis_type=_hypothesis_type(rel),
            rationale=str(a.get("insight") or a.get("rationale") or f"Dreamer suggested {rel} based on structural similarity."),
            expected_decision_impact=str(
                a.get("decision_impact")
                or a.get("expected_decision_impact")
                or _expected_decision_impact(
                    str(a.get("source_title") or ""),
                    str(a.get("target_title") or ""),
                    rel,
                )
            ),
            extras={"retrieval_feedback": feedback},
        )
        k = _candidate_key(base)
        if k not in seen_keys:
            rows.append(base)
            seen_keys.add(k)
            added += 1

        # Retrieval-value proposal family: reviewed edge-value adjustment candidate.
        if float(a.get("grounding") or 0.0) >= 0.75 and float(a.get("confidence") or 0.0) >= 0.75:
            weight_delta = round(0.03 + 0.12 * float(a.get("confidence") or 0.0), 4)
            rv = _make_candidate_row(
                now=now,
                run_meta=run_meta,
                association=a,
                hypothesis_type="retrieval_value_candidate",
                rationale=(
                    f"High-confidence {rel} candidate may improve retrieval ordering when this edge is weighted"
                ),
                expected_decision_impact="Improve retrieval ranking for related mechanism/fact queries via reviewed weight adjustment",
                extras={
                    "proposed_weight_delta": weight_delta,
                    "retrieval_feedback": feedback,
                    "review_payload": {
                        "kind": "edge_weight_adjustment",
                        "source_bead_id": src,
                        "target_bead_id": tgt,
                        "relationship": rel,
                        "proposed_weight_delta": weight_delta,
                    },
                },
            )
            rk = _candidate_key(rv)
            if rk not in seen_keys:
                rows.append(rv)
                seen_keys.add(rk)
                added += 1

        # Entity merge proposal family from bead-linked entity ids.
        src_row = bead_map.get(src) if isinstance(bead_map, dict) else None
        tgt_row = bead_map.get(tgt) if isinstance(bead_map, dict) else None
        src_entity_ids = [str(x) for x in ((src_row or {}).get("entity_ids") or []) if str(x).strip()]
        tgt_entity_ids = [str(x) for x in ((tgt_row or {}).get("entity_ids") or []) if str(x).strip()]
        best_merge: tuple[float, str, str, list[str]] | None = None
        for left in src_entity_ids:
            for right in tgt_entity_ids:
                if left == right:
                    continue
                e1 = dict((entity_map or {}).get(left) or {})
                e2 = dict((entity_map or {}).get(right) or {})
                if not e1 or not e2:
                    continue
                score, reasons = _entity_similarity(e1, e2)
                if score < 0.88:
                    continue
                cand = (float(score), left, right, reasons)
                if best_merge is None or cand[0] > best_merge[0]:
                    best_merge = cand

        if best_merge is not None:
            score, left, right, reasons = best_merge
            left_row = dict((entity_map or {}).get(left) or {})
            right_row = dict((entity_map or {}).get(right) or {})
            keep_suggestion = left if float(left_row.get("confidence") or 0.0) >= float(right_row.get("confidence") or 0.0) else right
            em = _make_candidate_row(
                now=now,
                run_meta=run_meta,
                association=a,
                hypothesis_type="entity_merge_candidate",
                rationale="Source/target entity aliases appear semantically identical and may be fragmented identity",
                expected_decision_impact="Reduce entity fragmentation for coreference and long-horizon retrieval",
                extras={
                    "source_entity_id": left,
                    "target_entity_id": right,
                    "entity_merge_score": round(score, 4),
                    "entity_merge_reasons": sorted(set(reasons)),
                    "retrieval_feedback": feedback,
                    "review_payload": {
                        "kind": "entity_merge",
                        "left_entity_id": left,
                        "right_entity_id": right,
                        "keep_suggestion": keep_suggestion,
                        "score": round(score, 4),
                    },
                },
            )
            ek = _candidate_key(em)
            if ek not in seen_keys:
                rows.append(em)
                seen_keys.add(ek)
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
        hypothesis_type = str(target.get("hypothesis_type") or "").strip().lower()

        if hypothesis_type == "entity_merge_candidate":
            from core_memory.entity.merge_flow import apply_entity_merge_direct

            left = str(target.get("source_entity_id") or "").strip()
            right = str(target.get("target_entity_id") or "").strip()
            keep = str((((target.get("review_payload") or {}) if isinstance(target.get("review_payload"), dict) else {}).get("keep_suggestion") or left or "")).strip()
            merge = right if keep == left else left
            if left and right and keep and merge and keep != merge:
                merge_out = apply_entity_merge_direct(
                    root,
                    keep_entity_id=keep,
                    merge_entity_id=merge,
                    reviewer=str(reviewer or ""),
                    notes=str(notes or ""),
                )
                applied = {
                    "ok": bool(merge_out.get("ok")),
                    "canonical_entry": "entity_merge_review_apply",
                    "application_mode": "entity_merge_apply" if bool(merge_out.get("ok")) else "entity_merge_failed",
                    "keep_entity_id": keep,
                    "merge_entity_id": merge,
                    "result": merge_out,
                }
            else:
                applied = {
                    "ok": False,
                    "canonical_entry": "entity_merge_review_apply",
                    "application_mode": "entity_merge_invalid",
                    "error": "missing_entity_ids",
                }
            _write_candidates(root, rows)
            return {
                "ok": True,
                "candidate_id": cid,
                "status": target.get("status"),
                "applied": applied,
                "path": str(_candidates_path(root)),
            }

        if hypothesis_type == "retrieval_value_candidate":
            from core_memory.runtime.retrieval_value_overrides import apply_retrieval_value_override

            rp = (target.get("review_payload") or {}) if isinstance(target.get("review_payload"), dict) else {}
            src = str(rp.get("source_bead_id") or target.get("source_bead_id") or "").strip()
            tgt = str(rp.get("target_bead_id") or target.get("target_bead_id") or "").strip()
            rel = str(rp.get("relationship") or target.get("relationship") or "supports").strip() or "supports"
            delta = float(rp.get("proposed_weight_delta") or target.get("proposed_weight_delta") or 0.0)

            ov = apply_retrieval_value_override(
                root,
                source_bead_id=src,
                target_bead_id=tgt,
                relationship=rel,
                proposed_weight_delta=delta,
                reviewer=str(reviewer or ""),
                notes=str(notes or ""),
                source_proposal_id=str(cid),
            )
            applied = {
                "ok": bool(ov.get("ok")),
                "canonical_entry": "retrieval_value_override_apply",
                "application_mode": "retrieval_value_override_apply" if bool(ov.get("ok")) else "retrieval_value_override_failed",
                "result": ov,
            }
            _write_candidates(root, rows)
            return {
                "ok": True,
                "candidate_id": cid,
                "status": target.get("status"),
                "applied": applied,
                "path": str(_candidates_path(root)),
            }

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


def submit_entity_merge_candidate(
    *,
    root: str | Path,
    source_entity_id: str,
    target_entity_id: str,
    source_bead_id: str = "",
    target_bead_id: str = "",
    confidence: float = 0.9,
    reviewer: str = "",
    rationale: str = "",
    notes: str = "",
    run_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    left = str(source_entity_id or "").strip()
    right = str(target_entity_id or "").strip()
    if not left or not right or left == right:
        return {"ok": False, "error": {"code": "invalid_entity_pair", "source_entity_id": left, "target_entity_id": right}}

    rows = _read_candidates(root)
    now = _now()
    run_meta = dict(run_metadata or {})
    assoc = {
        "source": str(source_bead_id or "").strip(),
        "target": str(target_bead_id or "").strip(),
        "relationship": "similar_pattern",
        "novelty": 0.0,
        "grounding": 0.0,
        "confidence": float(confidence or 0.0),
    }
    payload = {
        "kind": "entity_merge",
        "left_entity_id": left,
        "right_entity_id": right,
        "keep_suggestion": left,
        "score": float(confidence or 0.0),
        "manual_submission": True,
    }
    row = _make_candidate_row(
        now=now,
        run_meta=run_meta,
        association=assoc,
        hypothesis_type="entity_merge_candidate",
        rationale=str(rationale or "Manual entity merge proposal submitted via typed MCP write surface"),
        expected_decision_impact="Reduce entity fragmentation for coreference and long-horizon retrieval",
        extras={
            "source_entity_id": left,
            "target_entity_id": right,
            "entity_merge_score": round(float(confidence or 0.0), 4),
            "entity_merge_reasons": ["manual_submission"],
            "review_payload": payload,
            "submitted_by": str(reviewer or ""),
            "submission_notes": str(notes or ""),
        },
    )

    k = _candidate_key(row)
    if any(_candidate_key(r) == k and str(r.get("status") or "") in {"pending", "accepted"} for r in rows if isinstance(r, dict)):
        return {"ok": True, "duplicate": True, "candidate_id": None, "path": str(_candidates_path(root))}

    rows.append(row)
    _write_candidates(root, rows)
    return {
        "ok": True,
        "duplicate": False,
        "candidate_id": str(row.get("id") or ""),
        "status": str(row.get("status") or "pending"),
        "path": str(_candidates_path(root)),
    }


__all__ = [
    "enqueue_dreamer_candidates",
    "list_dreamer_candidates",
    "decide_dreamer_candidate",
    "submit_entity_merge_candidate",
]
