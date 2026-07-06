from __future__ import annotations

"""Goal lifecycle resolution pass.

This module is deliberately runtime-owned orchestration: it detects whether the
current outcome bead closes an explicitly visible candidate goal, emits a typed
association through the crawler/association path, then asks promotion machinery
to apply the candidate -> resolved transition.
"""

from typing import Any

from core_memory.association.crawler_contract import apply_crawler_updates, merge_crawler_updates
from core_memory.persistence.store import MemoryStore
from core_memory.persistence.promotion_service import resolve_goal_candidate_for_store
from core_memory.schema.promotion_contract import current_promotion_state


def _tokens(row: dict[str, Any]) -> set[str]:
    text = " ".join(
        [
            str(row.get("title") or ""),
            " ".join(str(x) for x in (row.get("summary") or [])),
            str(row.get("detail") or ""),
            " ".join(str(x) for x in (row.get("retrieval_facts") or [])),
            " ".join(str(x) for x in (row.get("success_criteria") or [])),
        ]
    )
    stop = {"the", "and", "for", "with", "that", "this", "from", "into", "done", "goal", "outcome"}
    return {
        t.strip(".,:;!?()[]{}\"'").lower()
        for t in text.replace("_", " ").replace("-", " ").split()
        if len(t.strip(".,:;!?()[]{}\"'").lower()) >= 4 and t.strip(".,:;!?()[]{}\"'").lower() not in stop
    }


def _tag_overlap(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    left = {str(x).strip().lower() for x in (a.get("tags") or []) if str(x).strip()}
    right = {str(x).strip().lower() for x in (b.get("tags") or []) if str(x).strip()}
    return sorted(left.intersection(right))


def _match_goal(outcome: dict[str, Any], goal: dict[str, Any]) -> tuple[bool, str, str, list[str]]:
    shared_tags = _tag_overlap(outcome, goal)
    shared_tokens = sorted(_tokens(outcome).intersection(_tokens(goal)))
    if shared_tags:
        return True, "goal_resolution_shared_tags", f"Outcome and candidate goal share tags: {', '.join(shared_tags[:6])}.", shared_tags
    if len(shared_tokens) >= 2:
        return True, "goal_resolution_token_overlap", f"Outcome and candidate goal share terms: {', '.join(shared_tokens[:6])}.", shared_tokens[:6]
    return False, "", "", []


def resolve_goals_for_turn(
    *,
    root: str,
    session_id: str,
    turn_id: str,
    outcome_bead_id: str,
    visible_bead_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve explicitly visible candidate goals matched by this turn's outcome bead.

    Cross-session scope is opt-in: only current session beads and caller-supplied
    visible/window bead ids are considered. No historical search is performed here.
    """
    oid = str(outcome_bead_id or "").strip()
    if not oid:
        return {"ok": True, "evaluated": 0, "resolved": 0, "associations_appended": 0, "reason": "missing_outcome"}

    store = MemoryStore(root)
    idx = store._read_json(store.beads_dir / "index.json")
    beads = idx.get("beads") or {}
    outcome = beads.get(oid)
    if not isinstance(outcome, dict):
        return {"ok": True, "evaluated": 0, "resolved": 0, "associations_appended": 0, "reason": "outcome_not_found"}

    # Dedicated outcome pass: require an outcome-like current bead/signal.
    btype = str(outcome.get("type") or "").strip().lower()
    if btype != "outcome" and not isinstance(outcome.get("memory_outcome"), dict):
        return {"ok": True, "evaluated": 0, "resolved": 0, "associations_appended": 0, "reason": "not_outcome"}

    explicit_visible = {str(x) for x in (visible_bead_ids or []) if str(x).strip()}
    session_visible = {
        str(bid)
        for bid, row in beads.items()
        if isinstance(row, dict) and str(row.get("session_id") or "") == str(session_id)
    }
    allowed = explicit_visible.union(session_visible)
    if oid:
        allowed.add(oid)

    candidates: list[tuple[str, dict[str, Any], str, str, list[str]]] = []
    for gid, goal in beads.items():
        if not isinstance(goal, dict) or str(gid) == oid:
            continue
        if str(gid) not in allowed:
            continue
        if str(goal.get("type") or "").strip().lower() != "goal":
            continue
        if current_promotion_state(goal) != "candidate":
            continue
        matched, code, text, evidence_terms = _match_goal(outcome, goal)
        if matched:
            candidates.append((str(gid), goal, code, text, evidence_terms))

    if not candidates:
        return {"ok": True, "evaluated": 0, "resolved": 0, "associations_appended": 0, "reason": "no_matching_goal"}

    associations = []
    visible_sorted = sorted(allowed)
    for gid, _goal, code, text, evidence_terms in candidates:
        associations.append(
            {
                "source_bead": oid,
                "target_bead": gid,
                "relationship": "resolves",
                "confidence": 0.82,
                "reason_code": code,
                "reason_text": text,
                "provenance": "model_inferred",
                "evidence_fields": ["tags", "title", "summary", "success_criteria"],
                "evidence_bead_ids": [oid, gid],
                "turn_id": str(turn_id or ""),
                "visible_bead_ids": visible_sorted,
                "judge_model": "heuristic-goal-resolution-v1",
                "prompt_version": "goal-resolution-v1",
                "rubric_version": "goal-resolution-v1",
                "evidence_terms": evidence_terms,
            }
        )

    queued = apply_crawler_updates(
        root=root,
        session_id=session_id,
        updates={"associations": associations},
        visible_bead_ids=visible_sorted,
    )
    merged = merge_crawler_updates(root=root, session_id=session_id)

    resolved = 0
    results = []
    for gid, _goal, code, _text, _terms in candidates:
        out = resolve_goal_candidate_for_store(
            store,
            goal_bead_id=gid,
            outcome_bead_id=oid,
            turn_id=str(turn_id or ""),
            reason=code,
            visible_bead_ids=visible_sorted,
        )
        results.append(out)
        if out.get("ok"):
            resolved += 1

    return {
        "ok": True,
        "evaluated": len(candidates),
        "resolved": resolved,
        "queued": queued,
        "merge": merged,
        "associations_appended": int((merged or {}).get("associations_appended") or 0),
        "results": results,
    }
