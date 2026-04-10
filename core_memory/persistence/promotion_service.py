from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Any

from .io_utils import append_jsonl
from .archive_index import append_archive_snapshot
from .io_utils import store_lock
from ..retrieval.lifecycle import mark_semantic_dirty
from ..policy.promotion_contract import (
    validate_transition,
    classify_signal,
    is_promotion_locked,
    current_promotion_state,
)


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
    """Refresh advisory recommendation fields for candidates."""
    with store_lock(store.root):
        index = store._read_json(store.beads_dir / "index.json")
        rows, threshold = store._candidate_recommendation_rows(index, query_text=query_text)
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        updated = 0
        auto_archived = 0
        decision_log = store.beads_dir / "events" / "promotion-decisions.jsonl"

        for row in rows[: max(1, int(limit))]:
            bid = str(row.get("bead_id") or "")
            bead = (index.get("beads") or {}).get(bid)
            if not bead:
                continue

            bead["promotion_score"] = row.get("promotion_score")
            bead["promotion_threshold"] = row.get("promotion_threshold")
            bead["promotion_recommendation"] = row.get("recommendation")
            bead["promotion_last_evaluated_at"] = now
            bead["promotion_last_query_overlap"] = row.get("query_overlap", 0)

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

            if auto_archive_hold and rec == "hold" and reinf == 0 and q_overlap == 0 and age_ok and current_promotion_state(bead) == "candidate":
                revision_id = f"rev-{uuid.uuid4().hex[:12]}"
                append_archive_snapshot(
                    store.root,
                    {
                        "bead_id": bid,
                        "revision_id": revision_id,
                        "archived_at": now,
                        "archived_from_status": bead.get("status"),
                        "snapshot": dict(bead),
                        "reason": "auto_archive_hold_same_turn",
                    },
                )
                bead["archive_ptr"] = {"revision_id": revision_id}
                bead["detail"] = ""
                bead["summary"] = (bead.get("summary") or [])[:2]
                bead["status"] = "archived"
                bead["demotion_reason"] = "auto_archive_hold_no_reinforcement_same_turn"
                bead["promotion_decision"] = "archive"
                bead["promotion_reason"] = "auto_archive_hold_same_turn"
                bead["promotion_decided_at"] = now
                append_jsonl(
                    decision_log,
                    {
                        "ts": now,
                        "bead_id": bid,
                        "before_status": "candidate",
                        "after_status": "archived",
                        "decision": "archive",
                        "reason": "auto_archive_hold_same_turn",
                        "considerations": ["no_reinforcement", "no_query_overlap", f"age_hours>={int(min_age_hours)}"],
                    },
                )
                auto_archived += 1

            index["beads"][bid] = bead
            updated += 1

        store._write_json(store.beads_dir / "index.json", index)
        return {
            "ok": True,
            "candidate_total": len(rows),
            "evaluated": updated,
            "auto_archived": auto_archived,
            "adaptive_threshold": round(threshold, 4),
        }


def decide_promotion_for_store(
    store: Any,
    *,
    bead_id: str,
    decision: str,
    reason: str = "",
    considerations: Optional[list[str]] = None,
) -> dict:
    """Apply agent-led promotion decision for a bead."""
    decision_n = str(decision or "").strip().lower()
    if decision_n not in {"promote", "keep_candidate", "archive"}:
        return {"ok": False, "error": "invalid_decision"}

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
            bead["summary"] = (bead.get("summary") or [])[:2]
            bead["status"] = "archived"
            bead["promotion_state"] = "null"
            bead["promotion_locked"] = False
            bead["demotion_reason"] = str(reason).strip()

        bead["promotion_decision"] = decision_n
        bead["promotion_decided_at"] = now
        bead["promotion_decision_turn_id"] = str((bead.get("source_turn_ids") or [""])[-1] or "")
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
                "before_status": before,
                "after_status": bead.get("status"),
                "decision": decision_n,
                "reason": str(reason or ""),
                "considerations": [str(c) for c in (considerations or [])][:8],
            },
        )

        mark_semantic_dirty(store.root, reason="decide_promotion")

        return {
            "ok": True,
            "bead_id": bead_id,
            "before_status": before,
            "after_status": bead.get("status"),
            "decision": decision_n,
        }


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


def decide_session_promotion_states_for_store(
    store: Any,
    *,
    session_id: str,
    visible_bead_ids: Optional[list[str]] = None,
    turn_id: str = "",
) -> dict:
    """Per-turn session decision pass: promoted|candidate|null for visible beads."""
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
        counts = {"promoted": 0, "candidate": 0, "null": 0, "locked": 0, "evaluated": 0}

        for bid in ids:
            bead = beads.get(bid) or {}
            counts["evaluated"] += 1

            locked = is_promotion_locked(bead)

            if locked:
                bead["status"] = "promoted"
                bead["promotion_state"] = "promoted"
                bead["promotion_locked"] = True
                bead["promotion_decision"] = "promoted_locked"
                bead["promotion_decided_at"] = now
                if turn_id:
                    bead["promotion_decision_turn_id"] = str(turn_id)
                counts["locked"] += 1
                counts["promoted"] += 1
                beads[bid] = bead
                continue

            decision = classify_signal(bead=bead)

            if decision == "promoted":
                bead["status"] = "promoted"
                bead["promotion_state"] = "promoted"
                bead["promotion_locked"] = True
                bead["promoted_at"] = str(bead.get("promoted_at") or now)
                bead["promotion_decision"] = "promote"
                bead["promotion_decided_at"] = now
                bead["promotion_reason"] = str(bead.get("promotion_reason") or "session_turn_evidence")
                bead["promotion_evidence"] = {
                    "reason": bead.get("promotion_reason"),
                }
                counts["promoted"] += 1
            elif decision == "candidate":
                if str(bead.get("status") or "").strip().lower() not in {"archived", "superseded"}:
                    bead["status"] = "open"
                bead["promotion_state"] = "candidate"
                bead["promotion_locked"] = False
                bead["promotion_decision"] = "keep_candidate"
                bead["promotion_decided_at"] = now
                counts["candidate"] += 1
            else:
                if str(bead.get("status") or "").strip().lower() not in {"promoted", "archived"}:
                    bead["status"] = "open"
                bead["promotion_state"] = "null"
                bead["promotion_locked"] = False
                bead["promotion_decision"] = "null"
                bead["promotion_decided_at"] = now
                counts["null"] += 1

            if turn_id:
                bead["promotion_decision_turn_id"] = str(turn_id)
            beads[bid] = bead

        index["beads"] = beads
        store._write_json(store.beads_dir / "index.json", index)
        mark_semantic_dirty(store.root, reason="decide_session_promotion_states")
        return {"ok": True, "session_id": str(session_id), "turn_id": str(turn_id or ""), "counts": counts, "evaluated_bead_ids": ids}


def promotion_kpis_for_store(store: Any, limit: int = 500) -> dict:
    """Report promotion decision volume, reasons, and rec-vs-decision alignment."""
    idx = store._read_json(store.beads_dir / "index.json")
    beads = idx.get("beads") or {}
    decision_log = store.beads_dir / "events" / "promotion-decisions.jsonl"

    decisions = []
    if decision_log.exists():
        with open(decision_log, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    decisions.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    decisions = decisions[-max(1, int(limit)) :]

    by_decision: dict[str, int] = {}
    reason_hist: dict[str, int] = {}
    aligned = 0
    compared = 0

    for d in decisions:
        dec = str(d.get("decision") or "")
        by_decision[dec] = by_decision.get(dec, 0) + 1
        reason = str(d.get("reason") or "").strip()
        if reason:
            reason_hist[reason] = reason_hist.get(reason, 0) + 1

        bid = str(d.get("bead_id") or "")
        bead = beads.get(bid) or {}
        rec = str(bead.get("promotion_recommendation") or "").strip().lower()
        if rec:
            compared += 1
            if (rec == "strong" and dec == "promote") or (rec == "hold" and dec in {"archive", "keep_candidate"}) or (rec == "review"):
                aligned += 1

    agreement = round(aligned / compared, 4) if compared else None

    return {
        "ok": True,
        "decision_count": len(decisions),
        "by_decision": by_decision,
        "top_reasons": sorted(reason_hist.items(), key=lambda kv: kv[1], reverse=True)[:20],
        "recommendation_alignment": {
            "compared": compared,
            "aligned": aligned,
            "agreement_rate": agreement,
        },
    }


def rebalance_promotions_for_store(store: Any, apply: bool = False) -> dict:
    """Phase B: score promoted beads and demote weakly-supported promotions."""
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

        applied = 0
        if apply:
            for row in demote:
                bid = row["bead_id"]
                bead = index["beads"].get(bid)
                if not bead:
                    continue
                revision_id = f"rev-{uuid.uuid4().hex[:12]}"
                append_archive_snapshot(
                    store.root,
                    {
                        "bead_id": bid,
                        "revision_id": revision_id,
                        "archived_at": datetime.now(timezone.utc).isoformat(),
                        "archived_from_status": bead.get("status"),
                        "snapshot": dict(bead),
                    },
                )
                bead["archive_ptr"] = {"revision_id": revision_id}
                bead["detail"] = ""
                bead["summary"] = (bead.get("summary") or [])[:2]
                bead["status"] = "archived"
                bead["demoted_at"] = datetime.now(timezone.utc).isoformat()
                bead["demotion_reason"] = "phase_b_rebalance"
                index["beads"][bid] = bead
                applied += 1

            store._write_json(store.beads_dir / "index.json", index)

        return {
            "ok": True,
            "promoted_total": len(promoted_ids),
            "adaptive_threshold": round(threshold, 4),
            "demote_candidates": len(demote),
            "applied": applied,
            "sample": demote[:50],
        }
