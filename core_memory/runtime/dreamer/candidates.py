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
    if rel == "proposed_theme":
        return "proposed_theme_candidate"
    return "association_candidate"


def _proposal_family(hypothesis_type: str) -> str:
    ht = str(hypothesis_type or "").strip().lower()
    if ht == "contradiction_candidate":
        return "contradiction"
    if ht == "entity_merge_candidate":
        return "entity_identity"
    if ht == "retrieval_value_candidate":
        return "retrieval_value"
    if ht == "proposed_theme_candidate":
        return "theme"
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
    if ht == "proposed_theme_candidate":
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
    ht = str(row.get("hypothesis_type") or "")
    if ht == "proposed_theme_candidate":
        related = sorted(str(b) for b in (row.get("related_bead_ids") or []) if str(b))
        return "|".join(["proposed_theme_candidate", str(row.get("relationship") or ""), *related])
    return "|".join(
        [
            ht,
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
        from core_memory.runtime.observability.retrieval_feedback import summarize_retrieval_feedback

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


def enqueue_synthesized_themes(
    root: str | Path,
    themes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write synthesized proposed_theme_candidates to the queue with deduplication and quarantine.

    Quarantine rule: any theme with fewer than 3 entries in related_bead_ids is dropped
    rather than written, since it has insufficient grounded evidence.
    """
    rows = _read_candidates(root)
    seen_keys = {_candidate_key(r) for r in rows if isinstance(r, dict)}
    added = 0
    quarantined = 0
    for t in list(themes or []):
        if not isinstance(t, dict):
            continue
        related = list(t.get("related_bead_ids") or [])
        if len(related) < 3:
            quarantined += 1
            continue
        k = _candidate_key(t)
        if k in seen_keys:
            continue
        rows.append(t)
        seen_keys.add(k)
        added += 1
    if added:
        _write_candidates(root, rows)
    return {
        "ok": True,
        "added": added,
        "quarantined": quarantined,
        "total": len(themes),
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
    resolution: str | None = None,
    scope_a: str | None = None,
    scope_b: str | None = None,
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

    resolution_n = str(resolution or "").strip().lower()

    # A contradiction review can be "deferred" without accepting/rejecting the
    # candidate — the user said "not now". Record that and write nothing.
    if resolution_n == "defer":
        target["status"] = "deferred"
        target["review_state"] = "deferred"
        target["decision"] = {
            "decision": "defer",
            "reviewer": str(reviewer or ""),
            "notes": str(notes or ""),
            "decided_at": _now(),
        }
        _write_candidates(root, rows)
        return {
            "ok": True,
            "candidate_id": cid,
            "status": target.get("status"),
            "applied": {"ok": True, "application_mode": "deferred_no_write"},
            "path": str(_candidates_path(root)),
        }

    _prior_status = str(target.get("status") or "").strip().lower()
    target["status"] = "accepted" if decision_n == "accept" else "rejected"
    target["decision"] = {
        "decision": decision_n,
        "reviewer": str(reviewer or ""),
        "notes": str(notes or ""),
        "decided_at": _now(),
    }
    if resolution_n:
        target["resolution"] = resolution_n

    # Best-effort myelination reward: a human/governance decision on a candidate
    # reinforces (accept) or weakens (reject) its concrete supporting edge. Only
    # on a first decision (not idempotent re-decisions), and never for Dreamer
    # findings themselves — only the decision counts (PRD §11.1).
    if _prior_status not in {"accepted", "rejected"}:
        try:
            from core_memory.runtime.observability.myelination_rewards import (
                reward_dreamer_candidate_decision,
            )

            reward_dreamer_candidate_decision(root, candidate=target, decision=decision_n)
        except Exception:
            pass

    applied = None
    if decision_n == "accept" and apply:
        hypothesis_type = str(target.get("hypothesis_type") or "").strip().lower()

        if hypothesis_type == "narrative_candidate":
            # Storyline overlay materialisation — observer contract: writes ONE
            # overlay record to .beads/overlays.jsonl and nothing else. No
            # beads, no associations, no claims; the backbone is untouched.
            from core_memory.graph.storylines import overlays_path, read_active_overlays, read_all_overlays
            from core_memory.persistence.io_utils import append_jsonl
            from core_memory.schema.storyline_overlay import (
                build_storyline_overlay,
                validate_storyline_overlay,
            )

            # Idempotency: one decision materialises at most one overlay. A
            # retried accept (client timeout, double-submit) must return the
            # original application — never append a duplicate revision that
            # supersedes its own predecessor without new review.
            existing_overlay_id = str(target.get("applied_overlay_id") or "")
            if not existing_overlay_id:
                for prior in read_all_overlays(root):
                    meta = prior.get("run_metadata") if isinstance(prior.get("run_metadata"), dict) else {}
                    if str(meta.get("candidate_id") or "") == cid:
                        existing_overlay_id = str(prior.get("id") or "")
                        break
            if existing_overlay_id:
                target["applied_overlay_id"] = existing_overlay_id
                _write_candidates(root, rows)
                return {
                    "ok": True,
                    "candidate_id": cid,
                    "status": target.get("status"),
                    "applied": {
                        "ok": True,
                        "application_mode": "already_applied",
                        "overlay_id": existing_overlay_id,
                    },
                    "path": str(_candidates_path(root)),
                }

            conv_key = str(target.get("convergence_key") or "")
            predecessor_id: str | None = None
            if conv_key:
                for prior in read_active_overlays(root):
                    if str(prior.get("convergence_key") or "") == conv_key:
                        predecessor_id = str(prior.get("id") or "") or None
                        break

            overlay = build_storyline_overlay(
                kind="narrative",
                statement=str(target.get("statement") or target.get("rationale") or ""),
                supporting_worldline_ids=list(target.get("supporting_worldline_ids") or []),
                supporting_bead_ids=list(target.get("supporting_bead_ids") or []),
                confidence=float(target.get("confidence") or 0.0),
                convergence_key=conv_key,
                expected_revision_triggers=list(target.get("expected_revision_triggers") or []),
                supersedes_overlay_id=predecessor_id,
                run_metadata={
                    **dict(target.get("run_metadata") or {}),
                    "candidate_id": cid,
                    "reviewer": str(reviewer or ""),
                },
            )
            ok_overlay, err_code, err_details = validate_storyline_overlay(overlay)
            if not ok_overlay:
                _write_candidates(root, rows)
                return {
                    "ok": False,
                    "error": {"code": err_code, **err_details},
                    "candidate_id": cid,
                }
            append_jsonl(overlays_path(root), overlay)
            target["applied_overlay_id"] = overlay["id"]
            applied = {
                "ok": True,
                "application_mode": "storyline_overlay_written",
                "overlay_id": overlay["id"],
                "supersedes_overlay_id": predecessor_id,
            }
            _write_candidates(root, rows)
            return {
                "ok": True,
                "candidate_id": cid,
                "status": target.get("status"),
                "applied": applied,
                "path": str(_candidates_path(root)),
            }

        if hypothesis_type == "contradiction_pressure_candidate":
            from core_memory.claim.conflict_review import (
                RESOLUTION_BOTH_VALID,
                RESOLUTION_CHOICES,
                resolution_to_claim_updates,
            )
            from core_memory.claim.update_policy import emit_claim_updates
            from core_memory.persistence.store_claim_ops import (
                find_canonical_turn_bead_id,
                read_all_claim_rows,
                write_claims_to_bead,
            )
            from core_memory.runtime.engine import process_turn_finalized
            from core_memory.schema.turn import Turn

            if resolution_n not in RESOLUTION_CHOICES:
                _write_candidates(root, rows)
                return {
                    "ok": False,
                    "error": {
                        "code": "invalid_resolution",
                        "resolution": resolution_n,
                        "allowed": sorted(RESOLUTION_CHOICES),
                    },
                    "candidate_id": cid,
                }

            subject = str(target.get("subject") or "").strip()
            slot = str(target.get("slot") or "").strip()
            claim_a_id = str(target.get("claim_a_id") or "").strip()
            claim_b_id = str(target.get("claim_b_id") or "").strip()
            session_id = str(
                (((target.get("run_metadata") or {}) if isinstance(target.get("run_metadata"), dict) else {}).get("session_id") or "")
            ).strip() or "conflict-review"

            # --- both_valid: context-scoped fork --- #
            if resolution_n == RESOLUTION_BOTH_VALID:
                sa = str(scope_a or "").strip()
                sb = str(scope_b or "").strip()
                if not sa or not sb:
                    # Needs clarification — don't update candidate status.
                    target["status"] = "pending"
                    target.pop("decision", None)
                    target.pop("resolution", None)
                    missing = "scope_a" if not sa else "scope_b"
                    # Look up claim values for the prompt (best-effort).
                    all_claims_for_prompt, _ = read_all_claim_rows(str(root))
                    ca_dict = next((c for c in all_claims_for_prompt if str(c.get("id") or "") == claim_a_id), {})
                    cb_dict = next((c for c in all_claims_for_prompt if str(c.get("id") or "") == claim_b_id), {})
                    value_missing = str(cb_dict.get("value") or slot) if sa else str(ca_dict.get("value") or slot)
                    _write_candidates(root, rows)
                    return {
                        "ok": False,
                        "needs_clarification": True,
                        "missing": missing,
                        "prompt": (
                            f"When is \"{value_missing}\" still true? "
                            f"(Or say 'default / everywhere else' for the broader case.)"
                        ),
                        "candidate_id": cid,
                    }

                # Look up claim values.
                all_claims_bv, _ = read_all_claim_rows(str(root))
                ca_dict = next((c for c in all_claims_bv if str(c.get("id") or "") == claim_a_id), {})
                cb_dict = next((c for c in all_claims_bv if str(c.get("id") or "") == claim_b_id), {})
                value_a = str(ca_dict.get("value") or "")
                value_b = str(cb_dict.get("value") or "")

                # Write fork-event bead.
                fork_turn_id = f"both-valid-{cid}"
                fork_out = process_turn_finalized(
                    root=str(root),
                    session_id=session_id,
                    turn_id=fork_turn_id,
                    turns=[
                        Turn(speaker="user", role="user", content=(
                            f"Both values of {subject}:{slot} are true in different contexts: "
                            f"'{value_a}' when {sa}, '{value_b}' when {sb}."
                        )),
                        Turn(speaker="assistant", role="assistant", content=(
                            f"Recording context-scoped fork for {subject}:{slot}. "
                            f"'{value_a}' scoped to '{sa}'; '{value_b}' scoped to '{sb}'."
                        )),
                    ],
                    metadata={"context_scope_fork": {
                        "subject": subject, "slot": slot, "scope_a": sa, "scope_b": sb,
                    }},
                )
                fork_bead_id = find_canonical_turn_bead_id(str(root), session_id=session_id, turn_id=fork_turn_id) or fork_turn_id

                # Write two new context-scoped claims to the fork bead.
                import uuid as _uuid_bv
                now_iso = _now()
                new_a_id = str(_uuid_bv.uuid4())
                new_b_id = str(_uuid_bv.uuid4())
                write_claims_to_bead(str(root), fork_bead_id, [
                    {
                        "id": new_a_id,
                        "subject": subject,
                        "slot": slot,
                        "value": value_a,
                        "context_scope": sa,
                        "source_bead_id": fork_bead_id,
                        "created_at": now_iso,
                        "provenance": "both_valid_resolution",
                    },
                    {
                        "id": new_b_id,
                        "subject": subject,
                        "slot": slot,
                        "value": value_b,
                        "context_scope": sb,
                        "source_bead_id": fork_bead_id,
                        "created_at": now_iso,
                        "provenance": "both_valid_resolution",
                    },
                ])

                # Emit supersede updates: old global claims → new scoped claims.
                supersede_rows: list[dict] = []
                reason_text = str(notes or f"both_valid resolution: {subject}:{slot} context-scoped")
                if claim_a_id:
                    supersede_rows.append({
                        "id": str(_uuid_bv.uuid4()),
                        "decision": "supersede",
                        "target_claim_id": claim_a_id,
                        "replacement_claim_id": new_a_id,
                        "subject": subject,
                        "slot": slot,
                        "reason_text": reason_text,
                        "trigger_bead_id": fork_bead_id,
                        "provenance": "conflict_review_resolution",
                    })
                if claim_b_id:
                    supersede_rows.append({
                        "id": str(_uuid_bv.uuid4()),
                        "decision": "supersede",
                        "target_claim_id": claim_b_id,
                        "replacement_claim_id": new_b_id,
                        "subject": subject,
                        "slot": slot,
                        "reason_text": reason_text,
                        "trigger_bead_id": fork_bead_id,
                        "provenance": "conflict_review_resolution",
                    })

                written = 0
                if supersede_rows:
                    emitted = emit_claim_updates(
                        str(root), [], fork_bead_id,
                        session_id=session_id,
                        reviewed_updates={"claim_updates": supersede_rows},
                    ) or []
                    written = len(emitted)

                applied = {
                    "ok": bool(fork_out.get("ok")) and written > 0,
                    "canonical_entry": "context_scope_fork",
                    "application_mode": "context_scope_fork",
                    "scope_a": sa,
                    "scope_b": sb,
                    "fork_bead_id": fork_bead_id,
                    "claim_updates_written": written,
                    "turn_id": fork_turn_id,
                    "session_id": session_id,
                }
                target["decision"]["applied_turn_id"] = fork_turn_id
                target["review_state"] = "resolved"
                _write_candidates(root, rows)
                return {
                    "ok": True,
                    "candidate_id": cid,
                    "status": target.get("status"),
                    "applied": applied,
                    "path": str(_candidates_path(root)),
                }

            # --- prefer_a / prefer_b / retract_both path --- #
            turn_id = f"conflict-resolve-{cid}"

            # Record the user's decision as an audit turn (canonical write boundary).
            out = process_turn_finalized(
                root=str(root),
                session_id=session_id,
                turn_id=turn_id,
                turns=[
                    Turn(speaker="user", role="user", content=f"Resolve {subject}:{slot} contradiction: {resolution_n}."),
                    Turn(speaker="assistant", role="assistant", content=f"Applying reviewed claim resolution ({resolution_n}) for {subject}:{slot}."),
                ],
                metadata={"contradiction_resolution": {"subject": subject, "slot": slot, "resolution": resolution_n}},
            )

            # Resolve the canonical turn bead so the claim updates are grounded in a
            # real bead, then persist them through emit_claim_updates (the audit turn
            # itself authors no claims, so the turn-flow claim pass is skipped).
            trigger_bead_id = find_canonical_turn_bead_id(str(root), session_id=session_id, turn_id=turn_id) or turn_id
            claim_updates = resolution_to_claim_updates(
                resolution=resolution_n,
                subject=subject,
                slot=slot,
                claim_a_id=claim_a_id,
                claim_b_id=claim_b_id,
                trigger_bead_id=trigger_bead_id,
                reason=str(notes or f"User resolved {subject}:{slot} contradiction as {resolution_n}"),
            )
            written = 0
            if claim_updates:
                emitted = emit_claim_updates(
                    str(root), [], trigger_bead_id,
                    session_id=session_id,
                    reviewed_updates={"claim_updates": claim_updates},
                ) or []
                written = len(emitted)

            applied = {
                "ok": bool(out.get("ok")) and (written > 0 or not claim_updates),
                "canonical_entry": "emit_claim_updates",
                "application_mode": "claim_update_resolution" if written else "no_claim_update",
                "resolution": resolution_n,
                "turn_id": turn_id,
                "trigger_bead_id": trigger_bead_id,
                "session_id": session_id,
                "claim_updates_written": written,
            }
            target["decision"]["applied_turn_id"] = turn_id
            target["review_state"] = "resolved"

            _write_candidates(root, rows)
            return {
                "ok": True,
                "candidate_id": cid,
                "status": target.get("status"),
                "applied": applied,
                "path": str(_candidates_path(root)),
            }

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
            from core_memory.runtime.observability.retrieval_value_overrides import apply_retrieval_value_override

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

        if hypothesis_type == "proposed_theme_candidate":
            from core_memory.runtime.engine import process_turn_finalized
            from core_memory.schema.turn import Turn

            related_bead_ids = list(target.get("related_bead_ids") or [])
            if len(related_bead_ids) < 3:
                applied = {
                    "ok": False,
                    "canonical_entry": "process_turn_finalized",
                    "application_mode": "proposed_theme_quarantined",
                    "error": "related_bead_ids must contain at least 3 grounded bead IDs",
                }
                _write_candidates(root, rows)
                return {
                    "ok": True,
                    "candidate_id": cid,
                    "status": target.get("status"),
                    "applied": applied,
                    "path": str(_candidates_path(root)),
                }

            session_id = str(((target.get("run_metadata") or {}) if isinstance(target.get("run_metadata"), dict) else {}).get("session_id") or "").strip() or "dreamer-theme"
            turn_id = f"theme-apply-{cid}"
            rationale = str(target.get("rationale") or f"Accepted theme across {len(related_bead_ids)} beads")

            out = process_turn_finalized(
                root=str(root),
                session_id=session_id,
                turn_id=turn_id,
                turns=[
                    Turn(speaker="user", role="user", content=f"Accept proposed theme: {rationale}"),
                    Turn(speaker="assistant", role="assistant", content=f"Recording accepted theme. {rationale}"),
                ],
                metadata={
                    "proposed_theme": {
                        "type": "proposed_theme",
                        "related_bead_ids": related_bead_ids,
                        "confidence": float(target.get("confidence") or 0.0),
                        "relationship": str(target.get("relationship") or ""),
                        "generated_by": "dreamer",
                        "source_candidates": list(target.get("source_candidates") or []),
                        "status": "accepted",
                        "because": rationale,
                    }
                },
            )

            applied = {
                "ok": bool(out.get("ok")),
                "canonical_entry": "process_turn_finalized",
                "application_mode": "proposed_theme_bead_written",
                "turn_id": turn_id,
                "session_id": session_id,
                "related_bead_ids": related_bead_ids,
                "bead_type": "proposed_theme",
            }
            target["decision"]["applied_turn_id"] = turn_id
            _write_candidates(root, rows)
            return {
                "ok": True,
                "candidate_id": cid,
                "status": target.get("status"),
                "applied": applied,
                "path": str(_candidates_path(root)),
            }

        from core_memory.runtime.engine import process_turn_finalized
        from core_memory.schema.turn import Turn
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
                turns=[
                    Turn(speaker="user", role="user", content=f"Dreamer reviewer accepted candidate {cid}; apply reviewed association."),
                    Turn(speaker="assistant", role="assistant", content=f"Apply reviewed association: {src} {rel_apply} {tgt}. Rationale: {rationale}"),
                ],
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


def enqueue_contradiction_pressure_candidates(
    *,
    root: str | Path,
    conflicts: list[Any],
    threshold: float | None = None,
    run_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Emit contradiction_pressure_candidate rows for conflicts above threshold.

    ``conflicts`` is a list of ConflictItem instances or dicts with the same fields.
    The threshold defaults to the ``CORE_MEMORY_CONFLICT_REVIEW_THRESHOLD`` env var (0.7).
    """
    import os

    env_threshold = float(os.environ.get("CORE_MEMORY_CONFLICT_REVIEW_THRESHOLD") or "0.7")
    effective_threshold = float(threshold) if threshold is not None else env_threshold

    rows = _read_candidates(root)
    now = _now()
    run_meta = dict(run_metadata or {})
    # Map existing candidate key → row so we can return ids for pre-existing
    # candidates (recall needs the id to attach a review prompt, even on re-ask).
    existing_by_key: dict[str, dict[str, Any]] = {}
    for r in rows:
        if isinstance(r, dict):
            existing_by_key.setdefault(_candidate_key(r), r)

    added = 0
    candidate_ids: dict[str, str] = {}   # "subject:slot" → candidate_id (surfaceable)
    deferred_keys: list[str] = []        # "subject:slot" the user already deferred

    for conflict in list(conflicts or []):
        if hasattr(conflict, "to_dict"):
            c = conflict.to_dict()
        elif hasattr(conflict, "__dict__"):
            c = dict(conflict.__dict__)
        elif isinstance(conflict, dict):
            c = conflict
        else:
            continue

        score = float(c.get("epistemic_conflict_score") or 0.0)
        if score <= effective_threshold:
            continue

        subject = str(c.get("subject") or "").strip()
        slot = str(c.get("slot") or "").strip()
        claim_a_id = str(c.get("claim_a_id") or "").strip()
        claim_b_id = str(c.get("claim_b_id") or "").strip()
        if not subject or not slot:
            continue

        slot_key = f"{subject}:{slot}"
        assoc: dict[str, Any] = {
            "source": claim_a_id,
            "target": claim_b_id,
            "relationship": "contradicts",
            "novelty": 0.0,
            "grounding": score,
            "confidence": score,
        }
        row = _make_candidate_row(
            now=now,
            run_meta=run_meta,
            association=assoc,
            hypothesis_type="contradiction_pressure_candidate",
            rationale=(
                f"Claim conflict on {subject}:{slot} has epistemic pressure score {score:.3f} "
                f"(chain_seq_gap={c.get('chain_seq_gap', 0)}, conflict_since={c.get('conflict_since', '')}). "
                "Human review recommended."
            ),
            expected_decision_impact=(
                f"Resolve conflicting claims on '{subject}' / '{slot}' to improve recall accuracy."
            ),
            extras={
                "subject": subject,
                "slot": slot,
                "claim_a_id": claim_a_id,
                "claim_b_id": claim_b_id,
                "epistemic_conflict_score": score,
                "conflict_since": str(c.get("conflict_since") or ""),
                "chain_seq_gap": int(c.get("chain_seq_gap") or 0),
                "conflict_threshold": effective_threshold,
            },
        )
        # Override hypothesis_type — _make_candidate_row derives it from relationship,
        # so we set it explicitly after construction.
        row["hypothesis_type"] = "contradiction_pressure_candidate"
        row["proposal_family"] = "contradiction"
        row["benchmark_tags"] = ["contradiction_update", "current_state_factual"]

        k = _candidate_key(row)
        existing = existing_by_key.get(k)
        if existing is not None:
            # Already queued. Respect a prior "defer" — don't re-surface a prompt.
            state = str(existing.get("review_state") or existing.get("status") or "").strip().lower()
            if state in {"deferred", "rejected", "accepted"}:
                deferred_keys.append(slot_key)
            else:
                candidate_ids[slot_key] = str(existing.get("id") or "")
            continue

        rows.append(row)
        existing_by_key[k] = row
        candidate_ids[slot_key] = str(row.get("id") or "")
        added += 1

    if added:
        _write_candidates(root, rows)

    return {
        "ok": True,
        "added": added,
        "queue_depth": len(rows),
        "threshold": effective_threshold,
        "candidate_ids": candidate_ids,
        "deferred_keys": deferred_keys,
        "path": str(_candidates_path(root)),
    }


__all__ = [
    "enqueue_dreamer_candidates",
    "enqueue_contradiction_pressure_candidates",
    "list_dreamer_candidates",
    "decide_dreamer_candidate",
    "submit_entity_merge_candidate",
]
