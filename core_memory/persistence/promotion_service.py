from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ..schema.promotion import summary_truncation_limit
from ..schema.promotion_contract import (
    classify_signal,
    current_promotion_state,
    is_promotion_locked,
    validate_transition,
)
from ..persistence.semantic_lifecycle import mark_semantic_dirty
from .archive_index import append_archive_snapshot
from .io_utils import append_jsonl, store_lock


# Promotion scoring is useful deterministic advice, but it is not semantic
# authority.  These sources identify the explicit decision path that is
# allowed to advance canonical promotion state.
AGENT_PROMOTION_SOURCES = frozenset(
    {
        "inline_agent",
        "delegated_semantic_agent",
        "repair_agent",
        "explicit_agent_action",
    }
)


def _shadow_log_path(store: Any):
    return store.beads_dir / "events" / "promotion-shadow-recommendations.jsonl"


def _append_shadow_rows(store: Any, rows: list[dict[str, Any]]) -> None:
    """Persist advisory promotion evidence without changing any bead state."""
    path = _shadow_log_path(store)
    for row in rows:
        append_jsonl(path, row)


def _decision_for_review_state(value: Any) -> str | None:
    state = str(value or "").strip().lower()
    if state in {"promote", "promoted", "preserve_full_in_rolling", "mark_promoted"}:
        return "promote"
    if state in {"candidate", "keep_candidate"}:
        return "keep_candidate"
    if state == "archive":
        return "archive"
    return None


def _agent_authorship(source: Any, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {"source": str(source or "").strip()}
    for key in ("task_receipt_id", "task_id", "grounding_hash", "prompt_version", "rubric_version", "model_profile"):
        value = (metadata or {}).get(key)
        if value not in (None, "", {}, []):
            out[key] = value
    return out


def promotion_slate_for_store(store: Any, limit: int = 20, query_text: str = "") -> dict:
    """Build bounded candidate promotion slate with advisory recommendations."""
    index = store._read_json(store.beads_dir / "index.json")
    rows, threshold = store._candidate_recommendation_rows(index, query_text=query_text)
    return {
        "ok": True,
        "candidate_total": len(rows),
        "adaptive_threshold": round(threshold, 4),
        "query": query_text,
        "results": rows[: max(1, int(limit))],
    }


def evaluate_candidates_for_store(
    store: Any,
    *,
    limit: int = 50,
    query_text: str = "",
    auto_archive_hold: bool = False,
    min_age_hours: int = 12,
) -> dict:
    """Produce promotion recommendations without mutating canonical state.

    ``auto_archive_hold`` remains accepted for one compatibility window, but it
    now produces an advisory archive recommendation rather than changing a
    bead.  Canonical archive/promotion decisions must pass through
    :func:`decide_promotion_for_store` with recorded agent authorship.
    """
    with store_lock(store.root):
        index = store._read_json(store.beads_dir / "index.json")
        rows, threshold = store._candidate_recommendation_rows(index, query_text=query_text)
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        recommendations: list[dict[str, Any]] = []

        for row in rows[: max(1, int(limit))]:
            bid = str(row.get("bead_id") or "")
            bead = (index.get("beads") or {}).get(bid)
            if not bead:
                continue

            rec = str(row.get("recommendation") or "")
            reinf = int((row.get("reinforcement") or {}).get("count", 0))
            q_overlap = int(row.get("query_overlap", 0) or 0)
            age_ok = False
            created = str(bead.get("created_at") or "")
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    age_ok = ((now_dt - dt).total_seconds() / 3600.0) >= max(0, int(min_age_hours))
                except ValueError:
                    age_ok = True

            proposed = "archive" if (
                auto_archive_hold
                and rec == "hold"
                and reinf == 0
                and q_overlap == 0
                and age_ok
                and current_promotion_state(bead) == "candidate"
            ) else rec
            recommendations.append(
                {
                    "ts": now,
                    "kind": "promotion_shadow_recommendation",
                    "source": "heuristic_promotion_v1",
                    "session_id": str(bead.get("session_id") or ""),
                    "bead_id": bid,
                    "current_state": current_promotion_state(bead),
                    "recommendation": proposed,
                    "score": row.get("promotion_score"),
                    "threshold": row.get("promotion_threshold"),
                    "query_overlap": q_overlap,
                    "reinforcement": dict(row.get("reinforcement") or {}),
                    "reason": "auto_archive_hold_advisory" if proposed == "archive" else "candidate_score",
                }
            )

        _append_shadow_rows(store, recommendations)
        return {
            "ok": True,
            "candidate_total": len(rows),
            "evaluated": len(recommendations),
            "auto_archived": 0,
            "advisory_archive_candidates": sum(1 for row in recommendations if row.get("recommendation") == "archive"),
            "advisory_only": True,
            "adaptive_threshold": round(threshold, 4),
            "recommendations": recommendations[:50],
        }


def decide_promotion_for_store(
    store: Any,
    *,
    bead_id: str,
    decision: str,
    reason: str = "",
    considerations: Optional[list[str]] = None,
    authorship_source: str = "explicit_agent_action",
    authorship: dict[str, Any] | None = None,
    turn_id: str = "",
) -> dict:
    """Apply an explicit agent-led promotion decision for a bead.

    This is intentionally the only promotion function that changes canonical
    promotion state.  Scores and other deterministic signals are retained as
    advice, never as authority.
    """
    decision_n = str(decision or "").strip().lower()
    if decision_n not in {"promote", "keep_candidate", "archive"}:
        return {"ok": False, "error": "invalid_decision"}

    source = str(authorship_source or "").strip()
    if source not in AGENT_PROMOTION_SOURCES:
        return {"ok": False, "error": "agent_authorship_required", "authorship_source": source}

    if decision_n in {"promote", "archive"} and not str(reason or "").strip():
        return {"ok": False, "error": "reason_required_for_promote_or_archive"}

    with store_lock(store.root):
        index = store._read_json(store.beads_dir / "index.json")
        bead = (index.get("beads") or {}).get(bead_id)
        if not bead:
            return {"ok": False, "error": f"bead_not_found:{bead_id}"}

        before = str(bead.get("status") or "")
        now = datetime.now(timezone.utc).isoformat()

        # Canonical state normalization / policy enforcement.
        ok_transition, err = validate_transition(bead=bead, decision=decision_n)
        if not ok_transition:
            return {"ok": False, "error": err, "bead_id": bead_id, "decision": decision_n}

        # Snapshot advisory recommendation at decision time.
        score, factors = store._promotion_score(index, bead)
        threshold = store._adaptive_promotion_threshold(index)
        reinf = int((factors.get("reinforcement") or {}).get("count", 0))
        if score >= threshold and reinf >= 1:
            recommendation = "strong"
        elif score >= max(0.6, threshold - 0.08):
            recommendation = "review"
        else:
            recommendation = "hold"
        bead["promotion_score"] = round(score, 4)
        bead["promotion_threshold"] = round(threshold, 4)
        bead["promotion_recommendation"] = recommendation

        if decision_n == "promote":
            bead["status"] = "promoted"
            bead["promotion_state"] = "promoted"
            bead["promotion_locked"] = True
            bead["promoted_at"] = now
            bead["promotion_reason"] = str(reason).strip()
            bead["promotion_evidence"] = {
                "reason": str(reason).strip(),
                "score": round(score, 4),
                "threshold": round(threshold, 4),
            }
        elif decision_n == "keep_candidate":
            if str(bead.get("status") or "").strip().lower() not in {"archived", "superseded"}:
                bead["status"] = "open"
            bead["promotion_state"] = "candidate"
            bead["promotion_locked"] = False
        elif decision_n == "archive":
            revision_id = f"rev-{uuid.uuid4().hex[:12]}"
            append_archive_snapshot(
                store.root,
                {
                    "bead_id": bead_id,
                    "revision_id": revision_id,
                    "archived_at": now,
                    "archived_from_status": bead.get("status"),
                    "snapshot": dict(bead),
                    "reason": "agent_decision_archive",
                },
            )
            bead["archive_ptr"] = {"revision_id": revision_id}
            bead["detail"] = ""
            bead["summary"] = (bead.get("summary") or [])[:summary_truncation_limit(bead.get("type"))]
            bead["status"] = "archived"
            bead["promotion_state"] = "null"
            bead["promotion_locked"] = False
            bead["demotion_reason"] = str(reason).strip()

        bead["promotion_decision"] = decision_n
        bead["promotion_decided_at"] = now
        bead["promotion_decision_turn_id"] = str(turn_id or (bead.get("source_turn_ids") or [""])[-1] or "")
        bead["promotion_authorship"] = _agent_authorship(source, authorship)
        if considerations:
            bead["promotion_considerations"] = [str(c) for c in considerations][:8]

        index["beads"][bead_id] = bead
        store._write_json(store.beads_dir / "index.json", index)

        # append audit row
        decision_log = store.beads_dir / "events" / "promotion-decisions.jsonl"
        append_jsonl(
            decision_log,
            {
                "ts": now,
                "bead_id": bead_id,
                "session_id": str(bead.get("session_id") or ""),
                "before_status": before,
                "after_status": bead.get("status"),
                "decision": decision_n,
                "reason": str(reason or ""),
                "considerations": [str(c) for c in (considerations or [])][:8],
                "authorship": _agent_authorship(source, authorship),
                "turn_id": str(turn_id or ""),
            },
        )

        mark_semantic_dirty(store.root, reason="decide_promotion")

        return {
            "ok": True,
            "bead_id": bead_id,
            "before_status": before,
            "after_status": bead.get("status"),
            "decision": decision_n,
            "authorship_source": source,
        }


def resolve_goal_candidate_for_store(
    store: Any,
    *,
    goal_bead_id: str,
    outcome_bead_id: str,
    turn_id: str = "",
    reason: str = "goal_resolution_match",
    visible_bead_ids: Optional[list[str]] = None,
    authorship_source: str = "explicit_agent_action",
    authorship: dict[str, Any] | None = None,
) -> dict:
    """Apply an explicitly authorized goal-resolution decision."""
    gid = str(goal_bead_id or "").strip()
    oid = str(outcome_bead_id or "").strip()
    if not gid or not oid:
        return {"ok": False, "error": "missing_goal_or_outcome"}
    source = str(authorship_source or "").strip()
    if source not in AGENT_PROMOTION_SOURCES:
        return {"ok": False, "error": "agent_authorship_required", "authorship_source": source}

    with store_lock(store.root):
        index = store._read_json(store.beads_dir / "index.json")
        bead = (index.get("beads") or {}).get(gid)
        if not isinstance(bead, dict):
            return {"ok": False, "error": f"bead_not_found:{gid}"}
        if str(bead.get("type") or "").strip().lower() != "goal":
            return {"ok": False, "error": "not_goal", "bead_id": gid}
        if current_promotion_state(bead) != "candidate":
            return {"ok": False, "error": "goal_not_candidate", "bead_id": gid}

        before = str(bead.get("status") or "")
        now = datetime.now(timezone.utc).isoformat()
        visible = [str(x) for x in (visible_bead_ids or []) if str(x).strip()]
        bead["status"] = "resolved"
        bead["goal_status"] = "resolved"
        bead["promotion_state"] = "resolved"
        bead["promotion_locked"] = True
        bead["promotion_decision"] = "resolve_goal"
        bead["promotion_reason"] = str(reason or "goal_resolution_match")
        bead["promotion_decided_at"] = now
        bead["resolved_at"] = now
        bead["resolved_by_bead_id"] = oid
        if turn_id:
            bead["promotion_decision_turn_id"] = str(turn_id)
            bead["resolved_by_turn_id"] = str(turn_id)
        bead["promotion_evidence"] = {
            "reason": str(reason or "goal_resolution_match"),
            "outcome_bead_id": oid,
            "turn_id": str(turn_id or ""),
            "visible_bead_ids": visible,
        }
        bead["promotion_authorship"] = _agent_authorship(source, authorship)
        index["beads"][gid] = bead
        store._write_json(store.beads_dir / "index.json", index)

        decision_log = store.beads_dir / "events" / "promotion-decisions.jsonl"
        append_jsonl(
            decision_log,
            {
                "ts": now,
                "bead_id": gid,
                "before_status": before,
                "after_status": "resolved",
                "decision": "resolve_goal",
                "reason": str(reason or "goal_resolution_match"),
                "outcome_bead_id": oid,
                "turn_id": str(turn_id or ""),
                "visible_bead_ids": visible,
                "authorship": _agent_authorship(source, authorship),
            },
        )
        mark_semantic_dirty(store.root, reason="goal_lifecycle_resolved")

    # Best-effort myelination reward over the audited outcome--resolves-->goal
    # edge. Outside the lock; never fails resolution if reinforcement does.
    try:
        from core_memory.persistence.myelination_rewards import reward_goal_resolution

        reward_goal_resolution(store.root, goal_bead_id=gid, outcome_bead_id=oid, source_event_id=str(turn_id or gid))
    except Exception:
        pass
    return {"ok": True, "bead_id": gid, "before_status": before, "after_status": "resolved", "decision": "resolve_goal"}


def decide_promotion_bulk_for_store(store: Any, decisions: list[dict]) -> dict:
    """Apply a bounded batch of agent promotion decisions."""
    rows = decisions or []
    out = []
    for row in rows[:100]:
        out.append(
            decide_promotion_for_store(
                store,
                bead_id=str(row.get("bead_id") or row.get("id") or "").strip(),
                decision=str(row.get("decision") or "").strip(),
                reason=str(row.get("reason") or "").strip(),
                considerations=[str(x) for x in (row.get("considerations") or [])],
            )
        )
    return {
        "ok": True,
        "requested": len(rows),
        "applied": len(out),
        "results": out,
    }


def apply_agent_promotion_reviews_for_store(
    store: Any,
    *,
    reviewed_beads: list[dict[str, Any]] | None,
    session_id: str,
    turn_id: str,
    authorship: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply actionable promotion reviews from a typed authoring response.

    A review is not inferred from a score: it must carry one of the recognized
    decision states and be tied to inline/delegated/repair agent provenance.
    Non-actionable review rows are reported rather than coerced into state.
    """
    source = str((authorship or {}).get("source") or "").strip()
    rows = [dict(row) for row in (reviewed_beads or []) if isinstance(row, dict)]
    if source not in AGENT_PROMOTION_SOURCES:
        return {
            "ok": False,
            "error": "agent_authorship_required",
            "authorship_source": source,
            "requested": len(rows),
            "applied": 0,
            "results": [],
        }

    results: list[dict[str, Any]] = []
    skipped = 0
    for row in rows[:100]:
        bead_id = str(row.get("bead_id") or "").strip()
        decision = _decision_for_review_state(row.get("promotion_state"))
        if not bead_id or decision is None:
            skipped += 1
            continue
        reason = str(row.get("reason") or row.get("reason_text") or "").strip()
        result = decide_promotion_for_store(
            store,
            bead_id=bead_id,
            decision=decision,
            reason=reason,
            considerations=[str(item) for item in (row.get("considerations") or []) if str(item).strip()],
            authorship_source=source,
            authorship=authorship,
            turn_id=str(turn_id or ""),
        )
        result["review_state"] = str(row.get("promotion_state") or "")
        results.append(result)

    return {
        "ok": True,
        "session_id": str(session_id),
        "turn_id": str(turn_id or ""),
        "requested": len(rows),
        "applied": sum(1 for row in results if row.get("ok")),
        "skipped": skipped,
        "results": results,
        "authorship_source": source,
    }


def decide_session_promotion_states_for_store(
    store: Any,
    *,
    session_id: str,
    visible_bead_ids: Optional[list[str]] = None,
    turn_id: str = "",
) -> dict:
    """Produce per-turn heuristic promotion recommendations only.

    Historical versions rewrote ``promotion_state`` for every visible bead here.
    That made a deterministic classifier the semantic authority.  The same
    classifier now writes append-only shadow evidence; an authored
    ``reviewed_beads`` decision is applied separately through
    :func:`apply_agent_promotion_reviews_for_store`.
    """
    with store_lock(store.root):
        index = store._read_json(store.beads_dir / "index.json")
        beads = index.get("beads") or {}

        allowed = set(str(x) for x in (visible_bead_ids or []) if str(x).strip())
        ids = []
        for bid, bead in beads.items():
            if str((bead or {}).get("session_id") or "") != str(session_id):
                continue
            if allowed and str(bid) not in allowed:
                continue
            ids.append(str(bid))
        ids.sort(key=lambda bid: (str((beads.get(bid) or {}).get("created_at") or ""), bid))

        now = datetime.now(timezone.utc).isoformat()
        counts = {"promote": 0, "keep_candidate": 0, "hold": 0, "locked": 0, "evaluated": 0}
        recommendations: list[dict[str, Any]] = []

        for bid in ids:
            bead = beads.get(bid) or {}
            counts["evaluated"] += 1

            locked = is_promotion_locked(bead)
            if locked:
                counts["locked"] += 1
                proposed = "hold"
                reason = "promotion_locked"
            else:
                signal = classify_signal(bead=bead)
                proposed = "promote" if signal == "promoted" else "keep_candidate" if signal == "candidate" else "hold"
                reason = "session_signal"
            counts[proposed] = counts.get(proposed, 0) + 1
            recommendations.append(
                {
                    "ts": now,
                    "kind": "promotion_shadow_recommendation",
                    "source": "heuristic_session_decision_v1",
                    "session_id": str(session_id),
                    "turn_id": str(turn_id or ""),
                    "bead_id": bid,
                    "current_state": current_promotion_state(bead),
                    "recommendation": proposed,
                    "reason": reason,
                    "locked": locked,
                }
            )

        _append_shadow_rows(store, recommendations)
        return {
            "ok": True,
            "session_id": str(session_id),
            "turn_id": str(turn_id or ""),
            "counts": counts,
            "evaluated_bead_ids": ids,
            "recommendations": recommendations,
            "advisory_only": True,
        }


def promotion_kpis_for_store(store: Any, limit: int = 500) -> dict:
    """Report promotion decision provenance and the shadow-mode release gate."""
    idx = store._read_json(store.beads_dir / "index.json")
    beads = idx.get("beads") or {}
    decision_log = store.beads_dir / "events" / "promotion-decisions.jsonl"
    shadow_log = _shadow_log_path(store)

    def read_rows(path: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not path.exists():
            return rows
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows[-max(1, int(limit)) :]

    decisions = read_rows(decision_log)
    shadows = read_rows(shadow_log)

    by_decision: dict[str, int] = {}
    reason_hist: dict[str, int] = {}
    latest_shadow: dict[tuple[str, str], dict[str, Any]] = {}
    for row in shadows:
        key = (str(row.get("session_id") or ""), str(row.get("bead_id") or ""))
        if key[1]:
            latest_shadow[key] = row

    agent_decision_keys: set[tuple[str, str]] = set()
    reviewed_divergences = 0
    unresolved_high_severity = 0
    divergences: list[dict[str, Any]] = []

    for d in decisions:
        dec = str(d.get("decision") or "")
        by_decision[dec] = by_decision.get(dec, 0) + 1
        reason = str(d.get("reason") or "").strip()
        if reason:
            reason_hist[reason] = reason_hist.get(reason, 0) + 1

        bid = str(d.get("bead_id") or "")
        session_id = str(d.get("session_id") or (beads.get(bid) or {}).get("session_id") or "")
        source = str((d.get("authorship") or {}).get("source") or "")
        key = (session_id, bid)
        if bid and source in AGENT_PROMOTION_SOURCES:
            agent_decision_keys.add(key)
        shadow = latest_shadow.get(key)
        if shadow and bid:
            recommendation = str(shadow.get("recommendation") or "")
            diverged = (recommendation == "promote" and dec != "promote") or (
                recommendation == "keep_candidate" and dec not in {"keep_candidate", "promote"}
            ) or (recommendation == "archive" and dec != "archive")
            if diverged:
                reviewed_divergences += 1
                divergences.append(
                    {
                        "session_id": session_id,
                        "bead_id": bid,
                        "recommendation": recommendation,
                        "agent_decision": dec,
                        "reviewed": True,
                    }
                )

    eligible_keys = {
        key
        for key, row in latest_shadow.items()
        if str(row.get("recommendation") or "") in {"promote", "keep_candidate", "archive"}
    }
    completed_sessions = {session_id for session_id, _ in eligible_keys if session_id}
    covered = len(eligible_keys.intersection(agent_decision_keys))
    coverage = round(covered / len(eligible_keys), 4) if eligible_keys else 0.0

    # A high-severity heuristic-only promotion is a shadow recommendation to
    # promote with no corresponding agent decision.  It must be reviewed
    # before the rollout can ever be declared ready.
    for key, row in latest_shadow.items():
        if str(row.get("recommendation") or "") == "promote" and key not in agent_decision_keys:
            unresolved_high_severity += 1

    release_gate = {
        "completed_sessions": len(completed_sessions),
        "minimum_completed_sessions": 20,
        "promotion_eligible_beads": len(eligible_keys),
        "minimum_promotion_eligible_beads": 100,
        "agent_decision_coverage": coverage,
        "minimum_agent_decision_coverage": 0.99,
        "divergences_reviewed": reviewed_divergences,
        "unresolved_high_severity_heuristic_only_promotions": unresolved_high_severity,
    }
    release_gate["ready"] = bool(
        release_gate["completed_sessions"] >= release_gate["minimum_completed_sessions"]
        and release_gate["promotion_eligible_beads"] >= release_gate["minimum_promotion_eligible_beads"]
        and release_gate["agent_decision_coverage"] >= release_gate["minimum_agent_decision_coverage"]
        and release_gate["unresolved_high_severity_heuristic_only_promotions"] == 0
    )

    return {
        "ok": True,
        "decision_count": len(decisions),
        "by_decision": by_decision,
        "top_reasons": sorted(reason_hist.items(), key=lambda kv: kv[1], reverse=True)[:20],
        "shadow_recommendation_count": len(shadows),
        "promotion_shadow": {
            "eligible": len(eligible_keys),
            "agent_decision_coverage": coverage,
            "divergences": divergences[:50],
            "divergences_reviewed": reviewed_divergences,
        },
        "release_gate": release_gate,
    }


def rebalance_promotions_for_store(store: Any, apply: bool = False) -> dict:
    """Return demotion recommendations without mutating promoted beads.

    ``apply`` is intentionally ignored: any demotion is a canonical promotion
    decision and must be issued through the explicit agent decision surface.
    """
    with store_lock(store.root):
        index = store._read_json(store.beads_dir / "index.json")
        promoted_ids = [bid for bid, b in (index.get("beads") or {}).items() if str(b.get("status") or "") == "promoted"]
        threshold = store._adaptive_promotion_threshold(index)
        demote: list[dict] = []

        for bid in promoted_ids:
            bead = index["beads"][bid]
            score, factors = store._promotion_score(index, bead)
            reinf = int((factors.get("reinforcement") or {}).get("count", 0))
            if score < threshold and reinf == 0 and str(bead.get("type") or "") not in {"session_end", "session_start"}:
                demote.append({"bead_id": bid, "score": round(score, 4), "reinforcement": reinf})

        advisory_rows = [
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "kind": "promotion_shadow_recommendation",
                "source": "heuristic_rebalance_v1",
                "session_id": str((index["beads"].get(row["bead_id"]) or {}).get("session_id") or ""),
                "bead_id": row["bead_id"],
                "current_state": "promoted",
                "recommendation": "archive",
                "reason": "phase_b_rebalance",
                "score": row["score"],
                "reinforcement": row["reinforcement"],
            }
            for row in demote
        ]
        _append_shadow_rows(store, advisory_rows)

        return {
            "ok": True,
            "promoted_total": len(promoted_ids),
            "adaptive_threshold": round(threshold, 4),
            "demote_candidates": len(demote),
            "applied": 0,
            "apply_ignored": bool(apply),
            "advisory_only": True,
            "sample": demote[:50],
        }
