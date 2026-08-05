"""Query-time planning over the durable junction roadmap.

The roadmap is a read-side projection of accepted graph structure.  This module
may filter, score, and stitch observed segments, but it never authors an edge or
semantic relationship.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from core_memory.graph import root_cause as causal_graph
from core_memory.persistence.junction_roadmap import read_junction_roadmap
from core_memory.retrieval.segments import segment_between

STITCHED_PLAN_SCHEMA = "core_memory.stitched_plan.v1"

JUNCTION_COSTS = {
    "exact_bead": 0.0,
    "claim_slot": 0.08,
    "goal": 0.14,
    "entity_worldline": 0.14,
    "embedding": 0.28,
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _clean_ids(values: Iterable[Any] | None) -> list[str]:
    return list(dict.fromkeys(_text(value) for value in values or [] if _text(value)))


def _read_index(root: Path) -> dict[str, Any]:
    try:
        payload = json.loads((root / ".beads" / "index.json").read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _temporal_frame(query: str, requested: str) -> str:
    frame = _text(requested).lower() or "auto"
    if frame != "auto":
        if frame not in {"historical", "current_truth"}:
            raise ValueError("temporal_frame must be auto, historical, or current_truth")
        return frame
    return (
        "historical"
        if re.search(r"\b(did|was|were|chose|chosen|decided|happened)\b", query.lower())
        else "current_truth"
    )


def _bead_is_scoped(
    bead: Mapping[str, Any],
    *,
    allowed: set[str],
    denied: set[str],
) -> bool:
    return bool(
        causal_graph._source_scope_allows(
            bead,
            allowed_source_ids=allowed,
            denied_source_ids=denied,
        )
    )


def _bead_is_temporally_eligible(bead: Mapping[str, Any], temporal_frame: str) -> bool:
    status = _text(bead.get("status")).lower()
    if status in {"deleted", "retracted", "inactive"}:
        return False
    if temporal_frame == "current_truth" and status == "superseded":
        return False
    return True


def _alternative_source_rows(alternative: Mapping[str, Any]) -> list[set[str]]:
    rows: list[set[str]] = []
    for edge_row in alternative.get("edge_cost_rows") or []:
        if not isinstance(edge_row, Mapping):
            continue
        refs = edge_row.get("dynamic_refs") or {}
        rows.append({_text(value) for value in refs.get("source_ids") or [] if _text(value)})
    return rows


def _alternative_is_scoped(
    alternative: Mapping[str, Any],
    *,
    allowed: set[str],
    denied: set[str],
) -> bool:
    rows = _alternative_source_rows(alternative)
    if any(tokens.intersection(denied) for tokens in rows):
        return False
    if allowed and (not rows or any(not tokens.intersection(allowed) for tokens in rows)):
        return False
    return True


def _goal_advancing_terminals(
    index: Mapping[str, Any],
    goal_ids: set[str],
    *,
    temporal_frame: str,
    allowed: set[str],
    denied: set[str],
) -> list[str]:
    beads = index.get("beads") if isinstance(index.get("beads"), Mapping) else {}
    terminals: list[str] = []
    for association in index.get("associations") or []:
        if not isinstance(association, Mapping):
            continue
        relationship = _text(association.get("relationship") or association.get("rel")).lower()
        status = _text(association.get("status") or "active").lower()
        if relationship != "advances_goal" or status not in {"active", "accepted", "validated"}:
            continue
        evidence_id = _text(association.get("source_bead_id") or association.get("source_bead"))
        goal_id = _text(association.get("target_bead_id") or association.get("target_bead"))
        evidence = beads.get(evidence_id) if isinstance(beads, Mapping) else None
        if goal_id not in goal_ids or not isinstance(evidence, Mapping):
            continue
        if (
            _bead_is_temporally_eligible(evidence, temporal_frame)
            and _bead_is_scoped(evidence, allowed=allowed, denied=denied)
        ):
            terminals.append(evidence_id)
    return sorted(set(terminals))


def _root_cause_terminals(
    root: Path,
    anchors: list[str],
    *,
    query: str,
    temporal_frame: str,
    allowed: set[str],
    denied: set[str],
) -> tuple[list[str], dict[str, Any]]:
    attribution = causal_graph.root_cause_trace(
        root,
        anchors,
        query=query,
        temporal_frame=temporal_frame,
        max_paths=12,
        max_causes=8,
        allowed_source_ids=sorted(allowed),
        denied_source_ids=sorted(denied),
    )
    terminals = [
        _text(path.get("terminal_cause_bead_id"))
        for path in attribution.get("causal_paths") or []
        if isinstance(path, Mapping)
        and _text(path.get("terminal_cause_bead_id"))
        and _text(path.get("terminal_cause_bead_id")) not in anchors
    ]
    return _clean_ids(terminals), attribution


def _dynamic_edge_components(
    row: Mapping[str, Any],
    *,
    beads: Mapping[str, Mapping[str, Any]],
    temporal_frame: str,
) -> tuple[dict[str, float], dict[str, Any]]:
    from_id = _text(row.get("from_bead_id"))
    to_id = _text(row.get("to_bead_id"))
    cause = dict(beads.get(from_id) or {})
    effect = dict(beads.get(to_id) or {})
    relationship = _text(row.get("relationship"))
    temporal_cost, temporal = causal_graph._temporal_penalty(effect, cause, relationship)
    claim_cost, historical, current, claim_summary, claim_flags = causal_graph._claim_state_cost(
        cause,
        temporal_frame,
    )
    refs = row.get("dynamic_refs") or {}
    contradiction = 0.45 if (
        relationship in causal_graph.CONFLICT_RELATIONS or refs.get("contradiction_refs")
    ) else 0.0
    has_evidence = bool(
        refs.get("evidence_refs")
        or refs.get("source_ids")
        or cause.get("source_ref")
        or cause.get("source_refs")
        or cause.get("hydration_ref")
    )
    components = {
        "temporal": round(float(temporal_cost), 6),
        "claim_state": round(float(claim_cost), 6),
        "contradiction": round(float(contradiction), 6),
        "permission_evidence_gap": 0.0 if has_evidence else 0.08,
    }
    return components, {
        "historical_confidence": historical,
        "current_truth_confidence": current,
        "claim_state_summary": claim_summary,
        "conflict_flags": claim_flags,
        "temporal": temporal,
    }


def _semantic_drag(query: str, bead_ids: list[str], beads: Mapping[str, Mapping[str, Any]]) -> tuple[float, float]:
    query_tokens = causal_graph._tokens(query)
    if not query_tokens:
        return 0.0, 0.5
    scores = [
        causal_graph._semantic_relevance(query_tokens, set(), dict(beads.get(bead_id) or {}))
        for bead_id in bead_ids
    ]
    relevance = max(scores, default=0.0)
    return round(0.25 * max(0.0, 0.35 - relevance), 6), round(relevance, 6)


def _score_state(
    state: dict[str, Any],
    *,
    query: str,
    beads: Mapping[str, Mapping[str, Any]],
    temporal_frame: str,
) -> dict[str, Any]:
    edge_receipts: list[dict[str, Any]] = []
    historical: list[float] = []
    current: list[float] = []
    edge_total = 0.0
    for row in state.get("edge_cost_rows") or []:
        cached = {
            _text(key): float(value or 0.0)
            for key, value in dict(row.get("cached_components") or {}).items()
        }
        dynamic, receipt = _dynamic_edge_components(
            row,
            beads=beads,
            temporal_frame=temporal_frame,
        )
        raw_cost = sum(cached.values()) + sum(dynamic.values())
        cost = max(0.001, raw_cost)
        edge_total += cost
        historical.append(float(receipt["historical_confidence"]))
        current.append(float(receipt["current_truth_confidence"]))
        edge_receipts.append(
            {
                "edge_id": _text(row.get("edge_id")),
                "from_bead_id": _text(row.get("from_bead_id")),
                "to_bead_id": _text(row.get("to_bead_id")),
                "relationship": _text(row.get("relationship")),
                "cached_components": cached,
                "dynamic_components": dynamic,
                "raw_cost": round(raw_cost, 6),
                "cost": round(cost, 6),
                **receipt,
            }
        )
    drag, semantic_score = _semantic_drag(query, state["bead_ids"], beads)
    state["edge_cost_rows"] = edge_receipts
    state["semantic_drag"] = drag
    state["semantic_relevance_score"] = semantic_score
    state["query_cost"] = round(edge_total + drag, 6)
    state["historical_confidence"] = round(sum(historical) / max(1, len(historical)), 6)
    state["current_truth_confidence"] = round(sum(current) / max(1, len(current)), 6)
    return state


def _slice_state(
    base: Mapping[str, Any],
    *,
    direction: str,
    entry_index: int,
    exit_index: int,
) -> dict[str, Any] | None:
    bead_ids = list(base.get("bead_ids") or [])
    edge_rows = [dict(row) for row in base.get("edge_cost_rows") or [] if isinstance(row, Mapping)]
    if direction == "downstream":
        low, high = entry_index, exit_index
    else:
        low, high = exit_index, entry_index
    if low < 0 or high >= len(bead_ids) or low >= high or len(edge_rows) < high:
        return None
    sliced_beads = bead_ids[low : high + 1]
    sliced_rows = edge_rows[low:high]
    suffix = hashlib.sha256(f"{entry_index}:{exit_index}".encode()).hexdigest()[:8]
    return {
        **dict(base),
        "state_id": f"{base['segment_id']}:{direction}:{suffix}",
        "bead_ids": sliced_beads,
        "edge_cost_rows": sliced_rows,
        "entry_bead_id": bead_ids[entry_index],
        "exit_bead_id": bead_ids[exit_index],
        "entry_junction_id": (
            base["start_junction_id"] if direction == "downstream" else base["end_junction_id"]
        ),
        "exit_junction_id": (
            base["end_junction_id"] if direction == "downstream" else base["start_junction_id"]
        ),
    }


def _states_for_alternative(
    base: Mapping[str, Any],
    *,
    direction: str,
    anchors: set[str],
    terminals: set[str],
) -> list[dict[str, Any]]:
    beads = list(base.get("bead_ids") or [])
    if len(beads) < 2:
        return []
    default_entry = 0 if direction == "downstream" else len(beads) - 1
    default_exit = len(beads) - 1 if direction == "downstream" else 0
    entry_indices = {default_entry}
    exit_indices = {default_exit}
    entry_indices.update(index for index, bead_id in enumerate(beads) if bead_id in anchors)
    exit_indices.update(index for index, bead_id in enumerate(beads) if bead_id in terminals)
    states: dict[str, dict[str, Any]] = {}
    for entry_index in entry_indices:
        for exit_index in exit_indices:
            if direction == "downstream" and entry_index >= exit_index:
                continue
            if direction == "upstream" and entry_index <= exit_index:
                continue
            state = _slice_state(
                base,
                direction=direction,
                entry_index=entry_index,
                exit_index=exit_index,
            )
            if state is None:
                continue
            if state["entry_bead_id"] in anchors:
                state["entry_junction_id"] = f"exact:{state['entry_bead_id']}"
            if state["exit_bead_id"] in terminals:
                state["exit_junction_id"] = f"exact:{state['exit_bead_id']}"
            states[state["state_id"]] = state
    return list(states.values())


def _roadmap_states(
    roadmap: Mapping[str, Any],
    *,
    direction: str,
    anchors: set[str],
    terminals: set[str],
    allowed: set[str],
    denied: set[str],
) -> tuple[list[dict[str, Any]], int]:
    states: list[dict[str, Any]] = []
    excluded = 0
    for edge in roadmap.get("edges") or []:
        if not isinstance(edge, Mapping):
            continue
        for alternative in edge.get("alternatives") or []:
            if not isinstance(alternative, Mapping):
                continue
            if not _alternative_is_scoped(alternative, allowed=allowed, denied=denied):
                excluded += 1
                continue
            base = {
                **dict(alternative),
                "start_junction_id": _text(edge.get("start_junction_id")),
                "end_junction_id": _text(edge.get("end_junction_id")),
            }
            states.extend(
                _states_for_alternative(
                    base,
                    direction=direction,
                    anchors=anchors,
                    terminals=terminals,
                )
            )
    return states, excluded


def _vertex_map(roadmap: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(vertex.get("id")): dict(vertex)
        for vertex in roadmap.get("vertices") or []
        if isinstance(vertex, Mapping) and _text(vertex.get("id"))
    }


def _junction_match(
    left_bead_id: str,
    right_bead_id: str,
    left_junction_id: str,
    right_junction_id: str,
    vertices: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    if left_bead_id == right_bead_id:
        return {
            "junction_id": f"exact:{left_bead_id}",
            "tier": "exact_bead",
            "is_seam": False,
            "junction_cost": 0.0,
        }
    if not left_junction_id or left_junction_id != right_junction_id:
        return None
    vertex = vertices.get(left_junction_id) or {}
    tier = _text(vertex.get("tier")) or "embedding"
    members = {_text(value) for value in vertex.get("bead_ids") or [] if _text(value)}
    if left_bead_id not in members or right_bead_id not in members:
        return None
    return {
        "junction_id": left_junction_id,
        "tier": tier,
        "is_seam": True,
        "junction_cost": float(JUNCTION_COSTS.get(tier, JUNCTION_COSTS["embedding"])),
    }


def _anchor_match(
    anchor_id: str,
    state: Mapping[str, Any],
    vertices: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    entry_id = _text(state.get("entry_bead_id"))
    if anchor_id == entry_id:
        return _junction_match(anchor_id, entry_id, "", "", vertices)
    junction_id = _text(state.get("entry_junction_id"))
    vertex = vertices.get(junction_id) or {}
    members = {_text(value) for value in vertex.get("bead_ids") or [] if _text(value)}
    if anchor_id not in members or entry_id not in members:
        return None
    tier = _text(vertex.get("tier")) or "embedding"
    return {
        "junction_id": junction_id,
        "tier": tier,
        "is_seam": True,
        "junction_cost": float(JUNCTION_COSTS.get(tier, JUNCTION_COSTS["embedding"])),
        "anchor_bead_id": anchor_id,
    }


def _shortest_stitched_path(
    states: list[dict[str, Any]],
    *,
    anchors: set[str],
    terminals: set[str],
    vertices: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float] | None:
    by_id = {_text(state.get("state_id")): state for state in states}
    distances: dict[str, float] = {}
    parents: dict[str, tuple[str | None, dict[str, Any]]] = {}
    queue: list[tuple[float, str]] = []
    for state_id, state in by_id.items():
        matches = [
            match
            for anchor_id in anchors
            if (match := _anchor_match(anchor_id, state, vertices)) is not None
        ]
        if not matches:
            continue
        match = min(matches, key=lambda row: float(row["junction_cost"]))
        cost = float(state["query_cost"]) + float(match["junction_cost"])
        if cost < distances.get(state_id, math.inf):
            distances[state_id] = cost
            parents[state_id] = (None, match)
            heapq.heappush(queue, (cost, state_id))

    settled: set[str] = set()
    terminal_state_id = ""
    while queue:
        cost, state_id = heapq.heappop(queue)
        if state_id in settled or cost != distances.get(state_id):
            continue
        settled.add(state_id)
        state = by_id[state_id]
        if _text(state.get("exit_bead_id")) in terminals:
            terminal_state_id = state_id
            break
        for next_id, outgoing in by_id.items():
            if next_id in settled or outgoing.get("segment_id") == state.get("segment_id"):
                continue
            match = _junction_match(
                _text(state.get("exit_bead_id")),
                _text(outgoing.get("entry_bead_id")),
                _text(state.get("exit_junction_id")),
                _text(outgoing.get("entry_junction_id")),
                vertices,
            )
            if match is None:
                continue
            candidate = cost + float(outgoing["query_cost"]) + float(match["junction_cost"])
            if candidate < distances.get(next_id, math.inf):
                distances[next_id] = candidate
                parents[next_id] = (state_id, match)
                heapq.heappush(queue, (candidate, next_id))
    if not terminal_state_id:
        return None

    selected: list[dict[str, Any]] = []
    junctions: list[dict[str, Any]] = []
    cursor: str | None = terminal_state_id
    while cursor is not None:
        selected.append(by_id[cursor])
        parent, junction = parents[cursor]
        junctions.append(junction)
        cursor = parent
    selected.reverse()
    junctions.reverse()
    return selected, junctions, distances[terminal_state_id]


def _fallback(
    root: Path,
    *,
    query: str,
    anchors: list[str],
    terminals: list[str],
    terminal_mode: str,
    goal_ids: list[str],
    direction: str,
    temporal_frame: str,
    allowed: set[str],
    denied: set[str],
    limitation: str,
    root_cause_attribution: dict[str, Any] | None = None,
    roadmap_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    for anchor_id in anchors:
        for terminal_id in terminals:
            candidate = segment_between(
                root,
                anchor_id,
                terminal_id,
                direction=direction,
                temporal_frame=temporal_frame,
                allowed_source_ids=sorted(allowed),
                denied_source_ids=sorted(denied),
            )
            if candidate is not None and (
                best is None
                or float(candidate.get("total_cost") or math.inf)
                < float(best.get("total_cost") or math.inf)
            ):
                best = candidate
    if root_cause_attribution is None:
        root_cause_attribution = causal_graph.root_cause_trace(
            root,
            anchors,
            query=query,
            temporal_frame=temporal_frame,
            allowed_source_ids=sorted(allowed),
            denied_source_ids=sorted(denied),
        )
    segments = []
    if best is not None:
        segments.append(
            {
                "segment_id": _text(best.get("segment_id")),
                "bead_ids": list(best.get("bead_ids") or []),
                "cost": float(best.get("total_cost") or 0.0),
                "semantic_relevance_score": None,
            }
        )
    goal_satisfied = bool(goal_ids and best is not None and terminals)
    return {
        "ok": True,
        "plan": {
            "schema_version": STITCHED_PLAN_SCHEMA,
            "stitched": False,
            "segments": segments,
            "junctions": [],
            "total_cost": float(best.get("total_cost") or 0.0) if best else None,
            "historical_confidence": float(best.get("confidence") or 0.0) if best else None,
            "current_truth_confidence": float(best.get("confidence") or 0.0) if best else None,
            "seam_count": 0,
            "terminal_mode": terminal_mode,
            "terminal_bead_ids": terminals,
            "goal_conditioning": {
                "goal_bead_ids": goal_ids,
                "terminal_evidence_ids": terminals if goal_ids else [],
                "satisfied": goal_satisfied,
                "status": "goal_satisfied" if goal_satisfied else ("goal_unsatisfied" if goal_ids else "not_requested"),
            },
            "fallback_used": True,
            "limitations": [limitation],
        },
        "roadmap_meta": dict(roadmap_meta or {}),
        "root_cause_attribution": root_cause_attribution,
    }


def _present_plan(
    selected: list[dict[str, Any]],
    junctions: list[dict[str, Any]],
    *,
    total_cost: float,
    direction: str,
    terminal_mode: str,
    terminals: list[str],
    goal_ids: list[str],
    roadmap_meta: Mapping[str, Any],
    excluded: int,
    root_cause_attribution: Mapping[str, Any],
    state_count: int,
    state_cap: int,
) -> dict[str, Any]:
    presentation = list(reversed(selected)) if direction == "upstream" else list(selected)
    segments = [
        {
            "segment_id": _text(state.get("segment_id")),
            "state_id": _text(state.get("state_id")),
            "bead_ids": list(state.get("bead_ids") or []),
            "cost": float(state.get("query_cost") or 0.0),
            "semantic_relevance_score": float(state.get("semantic_relevance_score") or 0.0),
            "semantic_drag": float(state.get("semantic_drag") or 0.0),
            "edge_cost_rows": list(state.get("edge_cost_rows") or []),
        }
        for state in presentation
    ]
    historical = [float(state.get("historical_confidence") or 0.0) for state in selected]
    current = [float(state.get("current_truth_confidence") or 0.0) for state in selected]
    reported_junctions = [junction for junction in junctions if junction.get("tier") != "exact_bead"]
    terminal_evidence = terminals if goal_ids else []
    return {
        "ok": True,
        "plan": {
            "schema_version": STITCHED_PLAN_SCHEMA,
            "stitched": len(selected) > 1 or any(junction.get("is_seam") for junction in reported_junctions),
            "segments": segments,
            "junctions": reported_junctions,
            "total_cost": round(total_cost, 6),
            "historical_confidence": round(sum(historical) / max(1, len(historical)), 6),
            "current_truth_confidence": round(sum(current) / max(1, len(current)), 6),
            "seam_count": sum(1 for junction in reported_junctions if junction.get("is_seam")),
            "terminal_mode": terminal_mode,
            "terminal_bead_ids": terminals,
            "goal_conditioning": {
                "goal_bead_ids": goal_ids,
                "terminal_evidence_ids": terminal_evidence,
                "satisfied": bool(goal_ids and _text(selected[-1].get("exit_bead_id")) in terminal_evidence),
                "status": "goal_satisfied" if goal_ids else "not_requested",
            },
            "fallback_used": False,
            "limitations": [],
        },
        "roadmap_meta": {
            **dict(roadmap_meta),
            "scope_excluded_alternative_count": excluded,
            "query_state_count": state_count,
            "query_state_cap": state_cap,
            "query_state_truncated": state_count > state_cap,
        },
        "root_cause_attribution": dict(root_cause_attribution),
    }


def plan_over_roadmap(
    root: str | Path,
    *,
    query: str,
    anchor_ids: list[str],
    destination_anchor_ids: list[str] | None = None,
    goal_bead_ids: list[str] | None = None,
    direction: str = "upstream",
    temporal_frame: str = "auto",
    allowed_source_ids: list[str] | None = None,
    denied_source_ids: list[str] | None = None,
    max_vertices: int = 200,
    roadmap: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Plan across observed roadmap segments, falling back to bounded expansion."""

    root_path = Path(root)
    anchors = _clean_ids(anchor_ids)
    destinations = _clean_ids(destination_anchor_ids)
    goals = _clean_ids(goal_bead_ids)
    if destinations and goals:
        raise ValueError("destination_anchor_ids and goal_bead_ids are mutually exclusive")
    direction_n = _text(direction).lower() or "upstream"
    if direction_n not in {"upstream", "downstream", "any"}:
        raise ValueError("direction must be upstream, downstream, or any")
    frame = _temporal_frame(query, temporal_frame)
    allowed = set(_clean_ids(allowed_source_ids))
    denied = set(_clean_ids(denied_source_ids))
    index = _read_index(root_path)
    beads = {
        _text(bead_id): bead
        for bead_id, bead in (index.get("beads") or {}).items()
        if isinstance(bead, Mapping)
    }
    anchors = [
        bead_id
        for bead_id in anchors
        if bead_id in beads
        and _bead_is_temporally_eligible(beads[bead_id], frame)
        and _bead_is_scoped(beads[bead_id], allowed=allowed, denied=denied)
    ]
    if not anchors:
        return _fallback(
            root_path,
            query=query,
            anchors=[],
            terminals=[],
            terminal_mode="none",
            goal_ids=goals,
            direction=direction_n,
            temporal_frame=frame,
            allowed=allowed,
            denied=denied,
            limitation="no_scoped_query_anchors",
        )

    attribution: dict[str, Any] | None = None
    if goals:
        terminals = _goal_advancing_terminals(
            index,
            set(goals),
            temporal_frame=frame,
            allowed=allowed,
            denied=denied,
        )
        terminal_mode = "goal_advancing_evidence"
    elif destinations:
        terminals = [
            bead_id
            for bead_id in destinations
            if bead_id in beads
            and _bead_is_temporally_eligible(beads[bead_id], frame)
            and _bead_is_scoped(beads[bead_id], allowed=allowed, denied=denied)
        ]
        terminal_mode = "explicit_destination"
    else:
        terminals, attribution = _root_cause_terminals(
            root_path,
            anchors,
            query=query,
            temporal_frame=frame,
            allowed=allowed,
            denied=denied,
        )
        terminal_mode = "root_cause_candidates"
    if not terminals:
        return _fallback(
            root_path,
            query=query,
            anchors=anchors,
            terminals=[],
            terminal_mode=terminal_mode,
            goal_ids=goals,
            direction=direction_n,
            temporal_frame=frame,
            allowed=allowed,
            denied=denied,
            limitation="no_scoped_exact_terminals",
            root_cause_attribution=attribution,
        )

    persisted = dict(roadmap) if roadmap is not None else read_junction_roadmap(root_path)
    roadmap_meta = dict(persisted.get("roadmap_meta") or {})
    stale = False
    if roadmap is None and bool(persisted.get("present")):
        from core_memory.graph.roadmap import roadmap_input_revision

        stale = _text(persisted.get("input_revision")) != roadmap_input_revision(root_path)
    if (
        not bool(persisted.get("present", roadmap is not None))
        or _text(persisted.get("status")) != "ready"
        or stale
        or not persisted.get("edges")
    ):
        reason = "junction_roadmap_stale" if stale else "junction_roadmap_unavailable_or_sparse"
        return _fallback(
            root_path,
            query=query,
            anchors=anchors,
            terminals=terminals,
            terminal_mode=terminal_mode,
            goal_ids=goals,
            direction=direction_n,
            temporal_frame=frame,
            allowed=allowed,
            denied=denied,
            limitation=reason,
            root_cause_attribution=attribution,
            roadmap_meta=roadmap_meta,
        )

    directions = ("upstream", "downstream") if direction_n == "any" else (direction_n,)
    vertices = _vertex_map(persisted)
    if attribution is None:
        attribution = causal_graph.root_cause_trace(
            root_path,
            anchors,
            query=query,
            temporal_frame=frame,
            allowed_source_ids=sorted(allowed),
            denied_source_ids=sorted(denied),
        )
    candidates: list[tuple[float, dict[str, Any]]] = []
    total_excluded = 0
    for orientation in directions:
        states, excluded = _roadmap_states(
            persisted,
            direction=orientation,
            anchors=set(anchors),
            terminals=set(terminals),
            allowed=allowed,
            denied=denied,
        )
        total_excluded = max(total_excluded, excluded)
        all_scored = [
            _score_state(state, query=query, beads=beads, temporal_frame=frame)
            for state in states
        ]
        state_cap = max(1, int(max_vertices))
        scored = sorted(
            all_scored,
            key=lambda state: (
                0
                if state.get("entry_bead_id") in anchors or state.get("exit_bead_id") in terminals
                else 1,
                float(state.get("query_cost") or math.inf),
                _text(state.get("state_id")),
            ),
        )[:state_cap]
        path = _shortest_stitched_path(
            scored,
            anchors=set(anchors),
            terminals=set(terminals),
            vertices=vertices,
        )
        if path is None:
            continue
        selected, junctions, total_cost = path
        candidates.append(
            (
                total_cost,
                _present_plan(
                    selected,
                    junctions,
                    total_cost=total_cost,
                    direction=orientation,
                    terminal_mode=terminal_mode,
                    terminals=terminals,
                    goal_ids=goals,
                    roadmap_meta=roadmap_meta,
                    excluded=excluded,
                    root_cause_attribution=attribution,
                    state_count=len(all_scored),
                    state_cap=state_cap,
                ),
            )
        )
    if candidates:
        return min(candidates, key=lambda row: row[0])[1]
    return _fallback(
        root_path,
        query=query,
        anchors=anchors,
        terminals=terminals,
        terminal_mode=terminal_mode,
        goal_ids=goals,
        direction=direction_n,
        temporal_frame=frame,
        allowed=allowed,
        denied=denied,
        limitation="scoped_roadmap_disconnected",
        root_cause_attribution=attribution,
        roadmap_meta={**roadmap_meta, "scope_excluded_alternative_count": total_excluded},
    )


__all__ = ["JUNCTION_COSTS", "STITCHED_PLAN_SCHEMA", "plan_over_roadmap"]
