from __future__ import annotations

from typing import Any


def reinforcement_signals_for_store(store: Any, index: dict, bead: dict) -> dict:
    bead_id = str(bead.get("id") or "")
    if not bead_id:
        return {"count": 0}

    bead_links = store._normalize_links(bead.get("links"))
    links_in = 0
    links_out = len(bead_links)
    for other in (index.get("beads") or {}).values():
        if other.get("id") == bead_id:
            continue
        if str(other.get("linked_bead_id") or "") == bead_id:
            links_in += 1
            continue
        for l in store._normalize_links(other.get("links")):
            if str((l or {}).get("bead_id") or "") == bead_id:
                links_in += 1
                break

    assoc_deg = 0
    for a in (index.get("associations") or []):
        if not (a.get("source_bead") == bead_id or a.get("target_bead") == bead_id):
            continue
        edge_class = str(a.get("edge_class") or "").lower()
        rel = str(a.get("relationship") or "").lower()
        if edge_class == "derived" and rel in {"shared_tag", "follows", "related"}:
            continue
        assoc_deg += 1

    recurrence = len(bead.get("source_turn_ids") or []) >= 2
    recalled = int(bead.get("recall_count") or 0) > 0

    cnt = 0
    for v in [links_in > 0 or links_out > 0, assoc_deg > 0, recurrence, recalled]:
        cnt += 1 if v else 0

    return {
        "links_in": links_in,
        "links_out": links_out,
        "association_degree": assoc_deg,
        "recurrence": recurrence,
        "recalled": recalled,
        "count": cnt,
    }


def append_autonomy_kpi_for_store(
    store: Any,
    *,
    run_id: str,
    repeat_failure: bool = False,
    contradiction_resolved: bool = False,
    contradiction_latency_turns: int = 0,
    unjustified_flip: bool = False,
    constraint_violation: bool = False,
    wrong_transfer: bool = False,
    goal_carryover: bool = False,
) -> dict:
    rec = {
        "run_id": run_id,
        "mode": "core_memory",
        "task_id": "autonomy_kpi",
        "result": "success",
        "steps": 0,
        "tool_calls": 0,
        "beads_created": 0,
        "beads_recalled": 0,
        "repeat_failure": bool(repeat_failure),
        "decision_conflicts": 1 if contradiction_resolved else 0,
        "unjustified_flips": 1 if unjustified_flip else 0,
        "rationale_recall_score": 0,
        "turns_processed": 1,
        "compression_ratio": 0.0,
        "phase": "autonomy",
        "kpi_contradiction_resolved": bool(contradiction_resolved),
        "kpi_contradiction_latency_turns": max(0, int(contradiction_latency_turns)),
        "kpi_constraint_violation": bool(constraint_violation),
        "kpi_wrong_transfer": bool(wrong_transfer),
        "kpi_goal_carryover": bool(goal_carryover),
    }
    return store.append_metric(rec)


__all__ = ["reinforcement_signals_for_store", "append_autonomy_kpi_for_store"]
