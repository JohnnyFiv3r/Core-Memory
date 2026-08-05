from __future__ import annotations

import hashlib
import heapq
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from core_memory.graph.junctions import derive_junction_projection
from core_memory.schema.normalization import normalize_relation_type, relation_family


TIMESTAMP_PRIORITY = (
    "observed_at",
    "event_time",
    "effective_from",
    "recorded_at",
    "source_created_at",
    "created_at",
    "last_activated_at",
)

UPSTREAM_FROM_SOURCE = {"caused_by", "blocked_by", "derived_from", "superseded_by", "documented_by", "informed_by"}
UPSTREAM_FROM_TARGET = {"causes", "led_to", "enabled", "enables", "unblocks", "supports", "resolves", "diagnoses"}
BIDIRECTIONAL_WEAK = {"associated_with", "related_to", "shared_entity", "refines", "applies_pattern_of", "similar_pattern"}
CONFLICT_RELATIONS = {"contradicts", "invalidates", "conflicts_with"}

RELATION_PRIOR_COST = {
    "caused_by": 0.05,
    "causes": 0.05,
    "led_to": 0.08,
    "enabled": 0.15,
    "enables": 0.15,
    "unblocks": 0.15,
    "blocked_by": 0.18,
    "blocks_unblocks": 0.18,
    "supports": 0.35,
    "derived_from": 0.25,
    "documented_by": 0.25,
    "informed_by": 0.30,
    "resolves": 0.32,
    "diagnoses": 0.32,
    "supersedes": 0.45,
    "superseded_by": 0.45,
    "refines": 0.50,
    "associated_with": 0.75,
    "similar_pattern": 0.75,
    "related_to": 0.75,
    "shared_entity": 0.80,
    "contradicts": 1.00,
    "invalidates": 1.00,
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _clean_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _tokens(value: Any) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", str(value or "").lower()) if t}


def _parse_dt(value: Any) -> datetime | None:
    s = _text(value)
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def timestamp_for_bead(bead: dict[str, Any]) -> tuple[str, str]:
    for key in TIMESTAMP_PRIORITY:
        value = _text(bead.get(key))
        if value and _parse_dt(value) is not None:
            return value, key
    return "", ""


def normalize_causal_hints(hints: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(hints or {})
    source_scope = dict(raw.get("source_scope") or {})
    relation_families = {_text(x).lower() for x in _clean_list(raw.get("relation_families")) if _text(x)}
    causal_labels = {normalize_relation_type(_text(x)) for x in _clean_list(raw.get("causal_labels")) if _text(x)}
    return {
        "bead_types": {_text(x).lower() for x in _clean_list(raw.get("bead_types")) if _text(x)},
        "relation_families": relation_families,
        "causal_labels": causal_labels,
        "causal_direction": _text(raw.get("causal_direction") or "upstream").lower() or "upstream",
        "keywords": [_text(x) for x in _clean_list(raw.get("keywords")) if _text(x)],
        "entities": [_text(x) for x in _clean_list(raw.get("entities")) if _text(x)],
        "anchor_ids": [_text(x) for x in _clean_list(raw.get("anchor_ids")) if _text(x)],
        "temporal_frame": _text(raw.get("temporal_frame") or "auto").lower() or "auto",
        "source_scope": {
            "allowed_source_ids": {_text(x) for x in _clean_list(source_scope.get("allowed_source_ids")) if _text(x)},
            "denied_source_ids": {_text(x) for x in _clean_list(source_scope.get("denied_source_ids")) if _text(x)},
            "redaction_policy": _text(source_scope.get("redaction_policy") or "redact_evidence") or "redact_evidence",
        },
    }


def _read_index(root: Path) -> dict[str, Any]:
    try:
        payload = json.loads((root / ".beads" / "index.json").read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _edge_id(src: str, dst: str, rel: str, source: str) -> str:
    return f"{source}:{src}:{rel}:{dst}"


def _edge_key(src: str, dst: str, rel: str) -> str:
    return f"{src}|{rel}|{dst}"


def _coerce_confidence(value: Any, default: float = 0.75) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return default


def _add_edge(edges: dict[str, dict[str, Any]], src: str, dst: str, rel: str, *, source: str, confidence: Any = None, metadata: dict[str, Any] | None = None) -> None:
    src = _text(src)
    dst = _text(dst)
    rel = normalize_relation_type(_text(rel) or "associated_with")
    if not src or not dst or src == dst:
        return
    eid = _edge_id(src, dst, rel, source)
    if eid in edges:
        return
    edges[eid] = {
        "edge_id": eid,
        "src": src,
        "dst": dst,
        "rel": rel,
        "source": source,
        "confidence": _coerce_confidence(confidence, 0.75),
        "metadata": dict(metadata or {}),
    }


def _build_edges(root: Path, index: dict[str, Any]) -> list[dict[str, Any]]:
    beads = index.get("beads") if isinstance(index.get("beads"), dict) else {}
    edges: dict[str, dict[str, Any]] = {}

    for assoc in _clean_list(index.get("associations")):
        if not isinstance(assoc, dict):
            continue
        status = _text(assoc.get("status") or "active").lower()
        # Active-association view — must match traversal/edge_lifecycle:
        # superseded edges are not current truth.
        if status in {"retracted", "superseded", "inactive"}:
            continue
        # Fold the edge lifecycle (reinforcement/decay) into the attribution
        # edge confidence so beam search and hop expansion share semantics
        # (clamped to [0,1] by _coerce_confidence).
        try:
            from core_memory.graph.edge_weights import effective_edge_multiplier
            base_conf = _coerce_confidence(assoc.get("confidence"), 0.75)
            conf: Any = base_conf * effective_edge_multiplier(assoc)
        except Exception:
            conf = assoc.get("confidence")
        _add_edge(
            edges,
            assoc.get("source_bead") or assoc.get("source_bead_id"),
            assoc.get("target_bead") or assoc.get("target_bead_id"),
            assoc.get("relationship") or assoc.get("rel"),
            source="association",
            confidence=conf,
            metadata=assoc,
        )

    for bid, bead in beads.items() if isinstance(beads, dict) else []:
        if not isinstance(bead, dict):
            continue
        for link in _clean_list(bead.get("links")):
            if not isinstance(link, dict):
                continue
            _add_edge(edges, bid, link.get("bead_id") or link.get("target_id"), link.get("type") or link.get("rel"), source="bead_link", metadata=link)
        for ref in _clean_list(bead.get("derived_from_bead_ids")):
            _add_edge(edges, bid, ref, "derived_from", source="derived_from_bead_ids", confidence=bead.get("confidence"))
        for ref in _clean_list(bead.get("derived_from")):
            ref_s = _text(ref)
            if ref_s in beads:
                _add_edge(edges, bid, ref_s, "derived_from", source="derived_from", confidence=bead.get("confidence"))

    try:
        from core_memory.graph.core import build_graph
        graph = build_graph(root, write_snapshot=False)
        for e in (graph.get("edge_head") or {}).values():
            if not isinstance(e, dict):
                continue
            if _text(e.get("class")) and _text(e.get("class")) != "structural":
                continue
            _add_edge(edges, e.get("src_id"), e.get("dst_id"), e.get("rel"), source="graph", confidence=e.get("confidence") or e.get("w"), metadata=e)
    except Exception:
        pass

    return list(edges.values())


def _relation_family(rel: str) -> str:
    return relation_family(rel)


def _candidate_text(bead: dict[str, Any]) -> str:
    parts: list[str] = [
        _text(bead.get("title")),
        " ".join(_text(x) for x in _clean_list(bead.get("summary"))),
        _text(bead.get("detail")),
        " ".join(_text(x) for x in _clean_list(bead.get("entities"))),
        " ".join(_text(x) for x in _clean_list(bead.get("entity_refs"))),
        " ".join(_text(x) for x in _clean_list(bead.get("tags"))),
        " ".join(_text(x) for x in _clean_list(bead.get("topics"))),
    ]
    return " ".join(x for x in parts if x)


def _semantic_relevance(query_tokens: set[str], hint_tokens: set[str], bead: dict[str, Any]) -> float:
    bead_tokens = _tokens(_candidate_text(bead))
    wanted = set(query_tokens) | set(hint_tokens)
    if not wanted:
        return 0.5
    if not bead_tokens:
        return 0.0
    overlap = len(wanted & bead_tokens)
    return max(0.0, min(1.0, overlap / max(1.0, math.sqrt(len(wanted) * len(bead_tokens)))))


def _claim_state_cost(bead: dict[str, Any], temporal_frame: str) -> tuple[float, float, float, dict[str, int], list[str]]:
    status = _text(bead.get("status")).lower()
    updates = [u for u in _clean_list(bead.get("claim_updates")) if isinstance(u, dict)]
    claims = [c for c in _clean_list(bead.get("claims")) if isinstance(c, dict)]
    summary = {"active": len(claims), "superseded": 0, "disputed": 0, "contradicted": 0, "amended": 0}
    flags: list[str] = []
    cost = 0.0
    historical = 0.72 if claims else 0.62
    current = historical

    if status == "superseded":
        summary["superseded"] += 1
        current -= 0.35
        cost += 0.22 if temporal_frame == "historical" else 0.45
        flags.append("superseded_bead")

    for update in updates:
        decision = _text(update.get("decision")).lower()
        if decision in {"supersede", "superseded"}:
            summary["superseded"] += 1
            current -= 0.22
            cost += 0.18
        elif decision in {"conflict", "dispute", "disputed"}:
            summary["disputed"] += 1
            current -= 0.28
            cost += 0.28
            flags.append("disputed_claim")
        elif decision in {"contradict", "contradicted", "invalidate", "retract"}:
            summary["contradicted"] += 1
            current -= 0.4
            historical -= 0.15
            cost += 0.4
            flags.append("contradicted_claim")
        elif decision in {"amend", "clarify"}:
            summary["amended"] += 1
            current -= 0.08
            cost += 0.08

    return cost, max(0.0, min(1.0, historical)), max(0.0, min(1.0, current)), summary, flags


def _temporal_penalty(effect: dict[str, Any], cause: dict[str, Any], rel: str) -> tuple[float, dict[str, Any]]:
    effect_ts, effect_field = timestamp_for_bead(effect)
    cause_ts, cause_field = timestamp_for_bead(cause)
    effect_dt = _parse_dt(effect_ts)
    cause_dt = _parse_dt(cause_ts)
    if not effect_dt or not cause_dt:
        return 0.0, {"status": "unknown", "effect_field": effect_field, "cause_field": cause_field}
    if cause_dt <= effect_dt:
        return 0.0, {"status": "plausible", "effect_field": effect_field, "cause_field": cause_field}
    penalty = 0.18 if rel in {"caused_by", "causes", "led_to"} else 0.35
    return penalty, {"status": "cause_after_effect", "effect_field": effect_field, "cause_field": cause_field}


def _upstream_edges(node: str, edges: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str]]:
    out: list[tuple[dict[str, Any], str]] = []
    for edge in edges:
        rel = normalize_relation_type(_text(edge.get("rel")))
        src = _text(edge.get("src"))
        dst = _text(edge.get("dst"))
        if rel in UPSTREAM_FROM_SOURCE and src == node and dst:
            out.append((edge, dst))
        elif rel in UPSTREAM_FROM_TARGET and dst == node and src:
            out.append((edge, src))
        elif rel in BIDIRECTIONAL_WEAK:
            if src == node and dst:
                out.append((edge, dst))
            elif dst == node and src:
                out.append((edge, src))
        elif rel in CONFLICT_RELATIONS:
            if src == node and dst:
                out.append((edge, dst))
            elif dst == node and src:
                out.append((edge, src))
    return out


def _downstream_edges(node: str, edges: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str]]:
    out: list[tuple[dict[str, Any], str]] = []
    for edge in edges:
        rel = normalize_relation_type(_text(edge.get("rel")))
        src = _text(edge.get("src"))
        dst = _text(edge.get("dst"))
        if rel in UPSTREAM_FROM_SOURCE and dst == node and src:
            out.append((edge, src))
        elif rel in UPSTREAM_FROM_TARGET and src == node and dst:
            out.append((edge, dst))
        elif rel in BIDIRECTIONAL_WEAK or rel in CONFLICT_RELATIONS:
            if src == node and dst:
                out.append((edge, dst))
            elif dst == node and src:
                out.append((edge, src))
    return out


def _source_tokens(bead: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in (
        "source_id",
        "source_ref",
        "source_event_id",
        "source_artifact_uri",
        "artifact_uri",
        "raw_source_object_id",
    ):
        value = _text(bead.get(key))
        if value:
            tokens.add(value)
    hydration = bead.get("hydration_ref") if isinstance(bead.get("hydration_ref"), dict) else {}
    for key in ("store", "ref", "id", "uri", "url"):
        value = _text(hydration.get(key))
        if value:
            tokens.add(value)
    for source in _clean_list(bead.get("source_refs")):
        if isinstance(source, dict):
            for key in ("source_id", "source_ref", "ref", "id", "uri", "url"):
                value = _text(source.get(key))
                if value:
                    tokens.add(value)
        elif _text(source):
            tokens.add(_text(source))
    return tokens


def _source_scope_allows(
    bead: Mapping[str, Any],
    *,
    allowed_source_ids: set[str],
    denied_source_ids: set[str],
) -> bool:
    tokens = _source_tokens(bead)
    if denied_source_ids and tokens.intersection(denied_source_ids):
        return False
    if allowed_source_ids and not tokens.intersection(allowed_source_ids):
        return False
    return True


def _edge_cost(
    edge: dict[str, Any],
    *,
    effect: dict[str, Any],
    cause: dict[str, Any],
    query_tokens: set[str],
    hint_tokens: set[str],
    hints: dict[str, Any],
    myelination_bonus: dict[str, float],
    temporal_frame: str,
    drag_enabled: bool = True,
) -> tuple[float, dict[str, Any]]:
    rel = normalize_relation_type(_text(edge.get("rel")))
    family = _relation_family(rel)
    semantic_score = _semantic_relevance(query_tokens, hint_tokens, cause)
    semantic_floor = 0.35
    semantic_penalty = 0.25 * max(0.0, semantic_floor - semantic_score) if drag_enabled else 0.0
    confidence = _coerce_confidence(edge.get("confidence"), 0.75)
    confidence_penalty = -math.log(max(0.001, min(1.0, confidence)))
    temporal_cost, temporal = _temporal_penalty(effect, cause, rel)
    claim_cost, historical, current, claim_summary, claim_flags = _claim_state_cost(cause, temporal_frame)
    contradiction = 0.45 if rel in CONFLICT_RELATIONS else 0.0
    evidence_refs = _clean_list(edge.get("evidence_refs") or (edge.get("metadata") or {}).get("evidence_refs"))
    has_evidence = bool(evidence_refs or cause.get("source_ref") or cause.get("source_refs") or cause.get("hydration_ref"))
    evidence_gap = 0.0 if has_evidence else 0.08
    evidence_bonus = 0.08 if has_evidence else 0.0
    authority = _text((edge.get("metadata") or {}).get("authority") or cause.get("authority")).lower()
    user_bonus = 0.08 if "user" in authority and "confirm" in authority else 0.0
    edge_bonus = float(myelination_bonus.get(_edge_key(_text(edge.get("src")), _text(edge.get("dst")), rel), 0.0) or 0.0)

    hint_bonus = 0.0
    if rel in hints.get("causal_labels", set()):
        hint_bonus += 0.05
    if family in hints.get("relation_families", set()):
        hint_bonus += 0.04

    raw_cost = (
        float(RELATION_PRIOR_COST.get(rel, 0.70))
        + confidence_penalty
        + temporal_cost
        + claim_cost
        + contradiction
        + evidence_gap
        + semantic_penalty
        - edge_bonus
        - evidence_bonus
        - user_bonus
        - hint_bonus
    )
    cost = max(0.001, raw_cost)
    return cost, {
        "relation_prior_cost": round(float(RELATION_PRIOR_COST.get(rel, 0.70)), 6),
        "confidence": round(confidence, 6),
        "confidence_penalty": round(confidence_penalty, 6),
        "temporal_penalty": round(temporal_cost, 6),
        "claim_state_penalty": round(claim_cost, 6),
        "contradiction_penalty": round(contradiction, 6),
        "evidence_gap_penalty": round(evidence_gap, 6),
        "semantic_relevance_score": round(semantic_score, 6),
        "semantic_similarity_floor": semantic_floor,
        "semantic_mismatch_penalty": round(semantic_penalty, 6),
        "semantic_drag_enabled": bool(drag_enabled),
        "myelination_bonus": round(edge_bonus, 6),
        "evidence_bonus": round(evidence_bonus, 6),
        "user_validation_bonus": round(user_bonus, 6),
        "hint_bonus": round(hint_bonus, 6),
        "total_cost": round(cost, 6),
        "relation_family": family,
        "historical_confidence": round(historical, 6),
        "current_truth_confidence": round(current, 6),
        "claim_state_summary": claim_summary,
        "conflict_flags": claim_flags + (["conflict_relation"] if rel in CONFLICT_RELATIONS else []),
        "evidence_refs": evidence_refs,
        "temporal": temporal,
    }


def _bead_summary(bead_id: str, bead: dict[str, Any]) -> dict[str, Any]:
    return {
        "bead_id": bead_id,
        "title": _text(bead.get("title")),
        "type": _text(bead.get("type")),
        "summary": " ".join(_text(x) for x in _clean_list(bead.get("summary"))[:2]),
        "observed_at": _text(bead.get("observed_at") or bead.get("effective_from") or bead.get("created_at")),
    }


def _confidence_from_cost(cost: float) -> float:
    return max(0.0, min(1.0, math.exp(-max(0.0, cost))))


def _path_record(path_id: str, anchor: str, nodes: list[str], hops: list[dict[str, Any]], beads: dict[str, dict[str, Any]], total_cost: float) -> dict[str, Any]:
    summaries = []
    claim_summary = {"active": 0, "superseded": 0, "disputed": 0, "contradicted": 0, "amended": 0}
    conflict_flags: list[str] = []
    evidence_refs: list[Any] = []
    min_sem = 1.0
    cold = 0
    hist_scores: list[float] = []
    current_scores: list[float] = []
    myelination = 0.0
    for node in nodes:
        summaries.append(_bead_summary(node, beads.get(node) or {}))
    for hop in hops:
        parts = dict(hop.get("cost_breakdown") or {})
        min_sem = min(min_sem, float(parts.get("semantic_relevance_score") or 0.0))
        if float(parts.get("semantic_relevance_score") or 0.0) < float(parts.get("semantic_similarity_floor") or 0.35):
            cold += 1
        for key in claim_summary:
            claim_summary[key] += int((parts.get("claim_state_summary") or {}).get(key) or 0)
        conflict_flags.extend([_text(x) for x in _clean_list(parts.get("conflict_flags")) if _text(x)])
        evidence_refs.extend(_clean_list(parts.get("evidence_refs")))
        hist_scores.append(float(parts.get("historical_confidence") or 0.0))
        current_scores.append(float(parts.get("current_truth_confidence") or 0.0))
        myelination += float(parts.get("myelination_bonus") or 0.0)
    terminal = nodes[-1] if nodes else anchor
    return {
        "path_id": path_id,
        "outcome_bead_id": anchor,
        "terminal_cause_bead_id": terminal,
        "total_cost": round(total_cost, 6),
        "confidence": round(_confidence_from_cost(total_cost), 6),
        "historical_confidence": round(sum(hist_scores) / max(1, len(hist_scores)), 6),
        "current_truth_confidence": round(sum(current_scores) / max(1, len(current_scores)), 6),
        "min_semantic_relevance_score": round(min_sem if hops else 0.0, 6),
        "semantic_cold_hop_count": cold,
        "claim_state_summary": claim_summary,
        "depth": max(0, len(nodes) - 1),
        "nodes": nodes,
        "beads": summaries,
        "edges": hops,
        "evidence_refs": evidence_refs,
        "conflict_flags": sorted(set(conflict_flags)),
        "myelination": round(myelination, 6),
        "max_depth_reached": False,
    }


def _normalized_hop(
    edge: dict[str, Any],
    *,
    cause_id: str,
    effect_id: str,
    direction: str,
    step_cost: float,
    breakdown: dict[str, Any],
) -> dict[str, Any]:
    return {
        "from": cause_id,
        "to": effect_id,
        "raw_src": edge.get("src"),
        "raw_dst": edge.get("dst"),
        "edge_id": edge.get("edge_id"),
        "relation": normalize_relation_type(_text(edge.get("rel"))),
        "normalized_direction": direction,
        "cost": round(step_cost, 6),
        "confidence": breakdown.get("confidence"),
        "candidate_bead_semantic_relevance_score": breakdown.get("semantic_relevance_score"),
        "semantic_mismatch_penalty": breakdown.get("semantic_mismatch_penalty"),
        "myelination_bonus": breakdown.get("myelination_bonus"),
        "evidence_refs": breakdown.get("evidence_refs") or [],
        "cost_breakdown": breakdown,
    }


def _parameterized_best_first_search(
    *,
    beads: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    anchors: list[str],
    terminal_set: set[str] | None,
    length_cap: int,
    direction: str,
    drag_enabled: bool,
    result_mode: str,
    query_tokens: set[str],
    hint_tokens: set[str],
    hints: dict[str, Any],
    myelination_bonus: dict[str, float],
    temporal_frame: str,
    relation_families: set[str] | None = None,
    allowed_source_ids: set[str] | None = None,
    denied_source_ids: set[str] | None = None,
    max_results: int = 20,
    beam_width: int | None = None,
    max_expansions: int = 5_000,
    max_queue_states: int = 50_000,
) -> dict[str, Any]:
    """Run root-cause and bounded segment search through one heap engine."""

    if direction not in {"upstream", "downstream"}:
        raise ValueError("direction must be upstream or downstream")
    if result_mode not in {"best", "frontier"}:
        raise ValueError("result_mode must be best or frontier")

    allowed = set(allowed_source_ids or set())
    denied = set(denied_source_ids or set())
    families = set(relation_families or set())
    heap: list[tuple[float, int, str, str, list[str], list[dict[str, Any]]]] = []
    counter = 0
    for anchor in anchors:
        bead = beads.get(anchor)
        if not isinstance(bead, dict):
            continue
        if (allowed or denied) and not _source_scope_allows(
            bead,
            allowed_source_ids=allowed,
            denied_source_ids=denied,
        ):
            continue
        counter += 1
        heapq.heappush(heap, (0.0, counter, anchor, anchor, [anchor], []))

    results: list[dict[str, Any]] = []
    seen_paths: set[tuple[str, ...]] = set()
    warnings: list[dict[str, str]] = []
    expansions = 0
    termination_reason = "exhausted"
    length_limit = max(1, int(length_cap))
    result_limit = max(1, int(max_results))
    expansion_limit = max(1, int(max_expansions))
    queue_limit = max(1, int(max_queue_states))

    while heap:
        if expansions >= expansion_limit:
            termination_reason = "expansion_cap"
            break
        if len(heap) > queue_limit:
            termination_reason = "memory_pressure"
            break

        cost, _, anchor, node, nodes, hops = heapq.heappop(heap)
        expansions += 1
        reached_terminal = bool(hops and terminal_set is not None and node in terminal_set)

        if terminal_set is None and hops:
            signature = tuple(nodes)
            if signature not in seen_paths:
                seen_paths.add(signature)
                results.append(
                    {
                        "anchor": anchor,
                        "nodes": nodes,
                        "hops": hops,
                        "total_cost": cost,
                        "max_depth_reached": len(hops) >= length_limit,
                    }
                )
                if len(results) >= result_limit:
                    termination_reason = "result_cap"
                    break
        elif reached_terminal:
            signature = tuple(nodes)
            if signature not in seen_paths:
                seen_paths.add(signature)
                results.append(
                    {
                        "anchor": anchor,
                        "nodes": nodes,
                        "hops": hops,
                        "total_cost": cost,
                        "max_depth_reached": len(hops) >= length_limit,
                    }
                )
            if result_mode == "best":
                termination_reason = "first_optimal_terminal"
                break
            # A segment ends at the terminal; do not grow paths through it.
            continue

        if len(hops) >= length_limit:
            continue

        candidates = _upstream_edges(node, edges) if direction == "upstream" else _downstream_edges(node, edges)
        if not candidates and not hops and terminal_set is None:
            warnings.append(
                {
                    "kind": f"no_{direction}_edges",
                    "message": f"No {direction} causal edges found for anchor {node}.",
                }
            )
            continue

        ranked_next: list[tuple[float, str, list[str], list[dict[str, Any]]]] = []
        for edge, adjacent in candidates:
            if adjacent in nodes:
                continue
            rel = normalize_relation_type(_text(edge.get("rel")))
            if families and _relation_family(rel) not in families:
                continue
            adjacent_bead = beads.get(adjacent)
            node_bead = beads.get(node)
            if not isinstance(adjacent_bead, dict) or not isinstance(node_bead, dict):
                continue
            if (allowed or denied) and not _source_scope_allows(
                adjacent_bead,
                allowed_source_ids=allowed,
                denied_source_ids=denied,
            ):
                continue

            if direction == "upstream":
                cause_id, cause_bead = adjacent, adjacent_bead
                effect_id, effect_bead = node, node_bead
            else:
                cause_id, cause_bead = node, node_bead
                effect_id, effect_bead = adjacent, adjacent_bead
            step_cost, breakdown = _edge_cost(
                edge,
                effect=effect_bead,
                cause=cause_bead,
                query_tokens=query_tokens,
                hint_tokens=hint_tokens,
                hints=hints,
                myelination_bonus=myelination_bonus,
                temporal_frame=temporal_frame,
                drag_enabled=drag_enabled,
            )
            hop = _normalized_hop(
                edge,
                cause_id=cause_id,
                effect_id=effect_id,
                direction=direction,
                step_cost=step_cost,
                breakdown=breakdown,
            )
            ranked_next.append((cost + step_cost, adjacent, nodes + [adjacent], hops + [hop]))

        ranked_next.sort(key=lambda row: (row[0], row[1]))
        if beam_width is not None:
            ranked_next = ranked_next[: max(1, int(beam_width))]
        for next_cost, adjacent, next_nodes, next_hops in ranked_next:
            counter += 1
            heapq.heappush(heap, (next_cost, counter, anchor, adjacent, next_nodes, next_hops))

    return {
        "results": results,
        "warnings": warnings,
        "expansions": expansions,
        "termination_reason": termination_reason,
        "queue_exhausted": not heap,
    }


def _trace_summary(path: dict[str, Any], beads: dict[str, dict[str, Any]]) -> str:
    terminal = beads.get(_text(path.get("terminal_cause_bead_id"))) or {}
    outcome = beads.get(_text(path.get("outcome_bead_id"))) or {}
    terminal_title = _text(terminal.get("title")) or _text(path.get("terminal_cause_bead_id"))
    outcome_title = _text(outcome.get("title")) or _text(path.get("outcome_bead_id"))
    return f"{terminal_title} is a plausible upstream driver of {outcome_title}."


def _rank_influence(paths: list[dict[str, Any]], beads: dict[str, dict[str, Any]], *, max_causes: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    influence: dict[str, float] = {}
    path_count: dict[str, int] = {}
    best_cost: dict[str, float] = {}
    max_depth: dict[str, int] = {}
    terminal_paths: list[dict[str, Any]] = []
    node_paths = [(p, tuple(_text(x) for x in _clean_list(p.get("nodes")) if _text(x))) for p in paths]
    for path, nodes_tuple in node_paths:
        if not nodes_tuple:
            continue
        is_prefix = any(
            len(other_nodes) > len(nodes_tuple) and other_nodes[: len(nodes_tuple)] == nodes_tuple
            for _, other_nodes in node_paths
        )
        if not is_prefix:
            terminal_paths.append(path)
    for path in (terminal_paths or paths):
        confidence = float(path.get("confidence") or 0.0)
        nodes = [_text(x) for x in _clean_list(path.get("nodes")) if _text(x)]
        for depth, node in enumerate(nodes[1:], start=1):
            mass = confidence * (0.80 ** max(0, depth - 1))
            influence[node] = influence.get(node, 0.0) + mass
            path_count[node] = path_count.get(node, 0) + 1
            best_cost[node] = min(best_cost.get(node, float("inf")), float(path.get("total_cost") or 0.0))
            max_depth[node] = max(max_depth.get(node, 0), depth)
    max_influence = max(influence.values() or [1.0])
    breakdown = []
    causes = []
    for node, score in sorted(influence.items(), key=lambda kv: (-kv[1], kv[0])):
        normalized = score / max_influence if max_influence else 0.0
        bead = beads.get(node) or {}
        depth_bonus = min(0.12, 0.03 * max_depth.get(node, 0))
        convergence_bonus = min(0.16, 0.04 * max(0, path_count.get(node, 1) - 1))
        root_score = max(0.0, min(1.0, normalized + depth_bonus + convergence_bonus - (0.03 * max(0, max_depth.get(node, 0) - 5))))
        item = {
            **_bead_summary(node, bead),
            "score": round(root_score, 6),
            "influence": round(normalized, 6),
            "best_path_cost": round(best_cost.get(node, 0.0), 6),
            "path_count": int(path_count.get(node, 0)),
            "depth": int(max_depth.get(node, 0)),
        }
        breakdown.append({"bead_id": node, "influence": round(normalized, 6)})
        causes.append(item)
    return causes[: max(1, int(max_causes))], breakdown


def _trace_package(paths: list[dict[str, Any]], beads: dict[str, dict[str, Any]], temporal_frame: str) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    seen_terminals: set[str] = set()
    candidate_order = sorted(paths, key=lambda p: (float(p.get("total_cost") or 0.0), -float(p.get("current_truth_confidence") or 0.0), _text(p.get("path_id"))))
    for path in candidate_order:
        terminal = _text(path.get("terminal_cause_bead_id"))
        if terminal in seen_terminals and len(selected) >= 3:
            continue
        seen_terminals.add(terminal)
        selected.append(path)
        if len(selected) >= 8:
            break
    traces = []
    for idx, path in enumerate(selected, start=1):
        reason = "lowest_cost_current_truth_path" if idx == 1 else "diverse_candidate_trace"
        if int(path.get("semantic_cold_hop_count") or 0) > 0:
            reason = "causally_plausible_with_semantic_drag"
        if path.get("conflict_flags"):
            reason = "disputed_alternate_trace"
        traces.append(
            {
                "trace_id": f"trace_{idx}",
                "selection_reason": reason,
                "path_id": path.get("path_id"),
                "summary": _trace_summary(path, beads),
                "total_cost": path.get("total_cost"),
                "historical_confidence": path.get("historical_confidence"),
                "current_truth_confidence": path.get("current_truth_confidence"),
                "min_semantic_relevance_score": path.get("min_semantic_relevance_score"),
                "semantic_cold_hop_count": path.get("semantic_cold_hop_count"),
                "myelination": path.get("myelination"),
                "claim_state_summary": path.get("claim_state_summary"),
                "evidence_refs": path.get("evidence_refs") or [],
                "conflict_flags": path.get("conflict_flags") or [],
            }
        )
    return {
        "schema_version": "core_memory.trace_package.v1",
        "temporal_frame": temporal_frame,
        "candidate_traces": traces,
        "adjudication_rules": [
            "Do not introduce causes outside candidate_traces.",
            "Distinguish historical confidence from current truth confidence.",
            "Cite bead ids, claim ids, and evidence refs for causal statements.",
        ],
    }


def _junction_members(
    anchor: str,
    *,
    projection: dict[str, Any],
    beads: dict[str, dict[str, Any]],
) -> set[str]:
    anchor_id = _text(anchor)
    members: set[str] = set()
    if anchor_id in beads:
        members.add(anchor_id)
        bead_row = next(
            (
                row
                for row in _clean_list(projection.get("beads"))
                if isinstance(row, dict) and _text(row.get("bead_id")) == anchor_id
            ),
            None,
        )
        if isinstance(bead_row, dict):
            members.update(
                _text(bead_id)
                for bead_id in _clean_list(bead_row.get("corroborating_bead_ids"))
                if _text(bead_id) in beads
            )
        return members

    for identity in _clean_list(projection.get("identities")):
        if not isinstance(identity, dict):
            continue
        identity_tokens = {
            _text(identity.get("id")),
            _text(identity.get("key")),
            _text(identity.get("label")),
        }
        if anchor_id not in identity_tokens:
            continue
        members.update(
            _text(bead_id)
            for bead_id in _clean_list(identity.get("bead_ids"))
            if _text(bead_id) in beads
        )
    return members


def _stable_reference(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        except Exception:
            pass
    return _text(value)


def _claim_refs(bead: Mapping[str, Any]) -> set[str]:
    refs: set[str] = set()
    for key in ("claim_id", "canonical_claim_id", "claim_ids", "claim_refs"):
        for value in _clean_list(bead.get(key)):
            ref = _stable_reference(value)
            if ref:
                refs.add(ref)
    for key in ("claims", "claim_updates"):
        for value in _clean_list(bead.get(key)):
            if not isinstance(value, dict):
                ref = _stable_reference(value)
            else:
                ref = _text(value.get("id") or value.get("claim_id"))
                if not ref:
                    subject = _text(value.get("subject_ref") or value.get("subject"))
                    slot = _text(value.get("slot") or value.get("predicate"))
                    ref = f"{subject}|{slot}" if subject or slot else _stable_reference(value)
            if ref:
                refs.add(ref)
    return refs


def _dynamic_cost_signature(
    nodes: list[str],
    hops: list[dict[str, Any]],
    beads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    claim_refs: set[str] = set()
    temporal_coverage: set[str] = set()
    source_footprint: set[str] = set()
    contradiction_refs: set[str] = set()
    evidence_refs: set[str] = set()
    for bead_id in nodes:
        bead = beads.get(bead_id) or {}
        claim_refs.update(_claim_refs(bead))
        timestamp, field = timestamp_for_bead(bead)
        temporal_coverage.add(f"{field or 'unknown'}:{timestamp or 'unknown'}")
        source_footprint.update(_source_tokens(bead))
        for value in _clean_list(bead.get("evidence_refs")):
            ref = _stable_reference(value)
            if ref:
                evidence_refs.add(ref)
    for hop in hops:
        breakdown = dict(hop.get("cost_breakdown") or {})
        for value in _clean_list(breakdown.get("evidence_refs")):
            ref = _stable_reference(value)
            if ref:
                evidence_refs.add(ref)
        if normalize_relation_type(_text(hop.get("relation"))) in CONFLICT_RELATIONS or breakdown.get("conflict_flags"):
            contradiction_refs.add(_text(hop.get("edge_id")) or _stable_reference(hop))
    signature = {
        "claim_refs": sorted(claim_refs),
        "temporal_coverage": sorted(temporal_coverage),
        "source_footprint": sorted(source_footprint),
        "contradiction_refs": sorted(contradiction_refs),
        "evidence_refs": sorted(evidence_refs),
    }
    encoded = json.dumps(signature, sort_keys=True, separators=(",", ":"))
    signature["partition_key"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
    return signature


def _segment_from_search_result(
    result: dict[str, Any],
    *,
    direction: str,
    start_set: set[str],
    terminal_set: set[str],
    beads: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    traversal_nodes = [_text(value) for value in result.get("nodes") or [] if _text(value)]
    traversal_hops = [dict(value) for value in result.get("hops") or [] if isinstance(value, dict)]
    if direction == "upstream":
        bead_ids = list(reversed(traversal_nodes))
        hops = list(reversed(traversal_hops))
        expected_start, expected_end = terminal_set, start_set
    else:
        bead_ids = traversal_nodes
        hops = traversal_hops
        expected_start, expected_end = start_set, terminal_set
    if len(bead_ids) < 2 or bead_ids[0] not in expected_start or bead_ids[-1] not in expected_end:
        return None
    segment_seed = json.dumps(
        {
            "direction": direction,
            "bead_ids": bead_ids,
            "edge_ids": [_text(hop.get("edge_id")) for hop in hops],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    # The heap ranks with full precision; the public receipt reconciles exactly
    # to its emitted per-edge ledger.
    total_cost = sum(float(hop.get("cost") or 0.0) for hop in hops)
    signature = _dynamic_cost_signature(bead_ids, hops, beads)
    return {
        "schema_version": "core_memory.causal_segment.v1",
        "segment_id": f"segment:{hashlib.sha256(segment_seed.encode('utf-8')).hexdigest()[:24]}",
        "direction": direction,
        "bead_ids": bead_ids,
        "edges": hops,
        "length": len(hops),
        "total_cost": round(total_cost, 6),
        "confidence": round(_confidence_from_cost(total_cost), 6),
        "dynamic_cost_signature": signature,
    }


def _frontier_metrics(segment: dict[str, Any]) -> tuple[float, int, float]:
    evidence_count = len((segment.get("dynamic_cost_signature") or {}).get("evidence_refs") or [])
    validation_quality = sum(
        float((edge.get("cost_breakdown") or {}).get("user_validation_bonus") or 0.0)
        + float((edge.get("cost_breakdown") or {}).get("evidence_bonus") or 0.0)
        for edge in segment.get("edges") or []
        if isinstance(edge, dict)
    )
    return float(segment.get("total_cost") or 0.0), evidence_count, validation_quality


def _dominates(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_cost, left_evidence, left_validation = _frontier_metrics(left)
    right_cost, right_evidence, right_validation = _frontier_metrics(right)
    weakly_better = (
        left_cost <= right_cost
        and left_evidence >= right_evidence
        and left_validation >= right_validation
    )
    strictly_better = (
        left_cost < right_cost
        or left_evidence > right_evidence
        or left_validation > right_validation
    )
    return weakly_better and strictly_better


def _insert_partition_frontier(
    current: list[dict[str, Any]],
    candidate: dict[str, Any],
    *,
    max_results: int,
) -> tuple[list[dict[str, Any]], bool]:
    if any(_dominates(existing, candidate) for existing in current):
        return current, False
    retained = [existing for existing in current if not _dominates(candidate, existing)]
    if not any(existing.get("segment_id") == candidate.get("segment_id") for existing in retained):
        retained.append(candidate)
    retained.sort(
        key=lambda row: (
            _frontier_metrics(row)[0],
            -_frontier_metrics(row)[1],
            -_frontier_metrics(row)[2],
            _text(row.get("segment_id")),
        )
    )
    truncated = len(retained) > max_results
    return retained[:max_results], truncated


def _segment_search_context(
    root: Path,
    anchor_a: str,
    anchor_b: str,
    *,
    projection: dict[str, Any] | None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any], set[str], set[str]]:
    index = _read_index(root)
    beads = {str(key): value for key, value in (index.get("beads") or {}).items() if isinstance(value, dict)}
    derived = projection or derive_junction_projection(root, include_beads=True)
    return (
        beads,
        _build_edges(root, index),
        derived,
        _junction_members(anchor_a, projection=derived, beads=beads),
        _junction_members(anchor_b, projection=derived, beads=beads),
    )


def _run_segment_orientation(
    *,
    beads: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    anchor_a_set: set[str],
    anchor_b_set: set[str],
    direction: str,
    temporal_frame: str,
    relation_families: set[str],
    allowed_source_ids: set[str],
    denied_source_ids: set[str],
    max_len: int,
    result_mode: str,
    max_expansions: int,
) -> dict[str, Any]:
    return _parameterized_best_first_search(
        beads=beads,
        edges=edges,
        anchors=sorted(anchor_a_set),
        terminal_set=set(anchor_b_set),
        length_cap=max_len,
        direction=direction,
        drag_enabled=False,
        result_mode=result_mode,
        query_tokens=set(),
        hint_tokens=set(),
        hints=normalize_causal_hints(None),
        myelination_bonus={},
        temporal_frame=temporal_frame,
        relation_families=relation_families,
        allowed_source_ids=allowed_source_ids,
        denied_source_ids=denied_source_ids,
        max_results=1,
        beam_width=None,
        max_expansions=max_expansions,
    )


def segment_between(
    root: Path,
    anchor_a: str,
    anchor_b: str,
    *,
    max_len: int = 6,
    direction: str = "upstream",
    temporal_frame: str = "auto",
    relation_families: list[str] | None = None,
    allowed_source_ids: list[str] | None = None,
    denied_source_ids: list[str] | None = None,
    projection: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the best real observed chain between two junction anchors."""

    requested_direction = _text(direction).lower() or "upstream"
    if requested_direction not in {"upstream", "downstream", "any"}:
        raise ValueError("direction must be upstream, downstream, or any")
    frame = "current_truth" if temporal_frame == "auto" else _text(temporal_frame)
    root_path = Path(root)
    beads, edges, _, anchor_a_set, anchor_b_set = _segment_search_context(
        root_path,
        anchor_a,
        anchor_b,
        projection=projection,
    )
    if not anchor_a_set or not anchor_b_set:
        return None
    families = {_text(value).lower() for value in relation_families or [] if _text(value)}
    allowed = {_text(value) for value in allowed_source_ids or [] if _text(value)}
    denied = {_text(value) for value in denied_source_ids or [] if _text(value)}
    segments: list[dict[str, Any]] = []
    orientations = ("upstream", "downstream") if requested_direction == "any" else (requested_direction,)
    for orientation in orientations:
        search = _run_segment_orientation(
            beads=beads,
            edges=edges,
            anchor_a_set=anchor_a_set,
            anchor_b_set=anchor_b_set,
            direction=orientation,
            temporal_frame=frame,
            relation_families=families,
            allowed_source_ids=allowed,
            denied_source_ids=denied,
            max_len=max_len,
            result_mode="best",
            max_expansions=5_000,
        )
        if not search["results"]:
            continue
        segment = _segment_from_search_result(
            search["results"][0],
            direction=orientation,
            start_set=anchor_a_set,
            terminal_set=anchor_b_set,
            beads=beads,
        )
        if segment is not None:
            segments.append(segment)
    if not segments:
        return None
    return min(segments, key=lambda row: (float(row["total_cost"]), _text(row["segment_id"])))


def segment_frontier_between(
    root: Path,
    anchor_a: str,
    anchor_b: str,
    *,
    max_len: int = 6,
    direction: str = "upstream",
    temporal_frame: str = "current_truth",
    relation_families: list[str] | None = None,
    allowed_source_ids: list[str] | None = None,
    denied_source_ids: list[str] | None = None,
    max_expansions: int = 5_000,
    max_partitions: int = 32,
    max_results_per_partition: int = 2,
    projection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a bounded, partition-aware frontier of observed causal chains."""

    requested_direction = _text(direction).lower() or "upstream"
    if requested_direction not in {"upstream", "downstream", "any"}:
        raise ValueError("direction must be upstream, downstream, or any")
    frame = "current_truth" if temporal_frame == "auto" else _text(temporal_frame)
    root_path = Path(root)
    beads, edges, derived, anchor_a_set, anchor_b_set = _segment_search_context(
        root_path,
        anchor_a,
        anchor_b,
        projection=projection,
    )
    families = {_text(value).lower() for value in relation_families or [] if _text(value)}
    allowed = {_text(value) for value in allowed_source_ids or [] if _text(value)}
    denied = {_text(value) for value in denied_source_ids or [] if _text(value)}
    orientations = ("upstream", "downstream") if requested_direction == "any" else (requested_direction,)
    raw_segments: list[dict[str, Any]] = []
    searches: list[dict[str, Any]] = []
    remaining_expansions = max(1, int(max_expansions))
    for orientation in orientations:
        if remaining_expansions <= 0:
            break
        search = _run_segment_orientation(
            beads=beads,
            edges=edges,
            anchor_a_set=anchor_a_set,
            anchor_b_set=anchor_b_set,
            direction=orientation,
            temporal_frame=frame,
            relation_families=families,
            allowed_source_ids=allowed,
            denied_source_ids=denied,
            max_len=max_len,
            result_mode="frontier",
            max_expansions=remaining_expansions,
        )
        searches.append(search)
        remaining_expansions -= int(search["expansions"])
        for result in search["results"]:
            segment = _segment_from_search_result(
                result,
                direction=orientation,
                start_set=anchor_a_set,
                terminal_set=anchor_b_set,
                beads=beads,
            )
            if segment is not None:
                raw_segments.append(segment)

    partitions: dict[str, list[dict[str, Any]]] = {}
    partition_keys_seen: set[str] = set()
    incomplete_reason = ""
    for segment in sorted(raw_segments, key=lambda row: (float(row["total_cost"]), _text(row["segment_id"]))):
        partition_key = _text((segment.get("dynamic_cost_signature") or {}).get("partition_key"))
        partition_keys_seen.add(partition_key)
        if partition_key not in partitions and len(partitions) >= max(1, int(max_partitions)):
            incomplete_reason = incomplete_reason or "partition_cap"
            continue
        retained, truncated = _insert_partition_frontier(
            partitions.get(partition_key, []),
            segment,
            max_results=max(1, int(max_results_per_partition)),
        )
        partitions[partition_key] = retained
        if truncated and not incomplete_reason:
            incomplete_reason = "result_cap"

    for search in searches:
        if search["termination_reason"] in {"memory_pressure", "expansion_cap"}:
            incomplete_reason = _text(search["termination_reason"])
            break
    if len(searches) < len(orientations) and not incomplete_reason:
        incomplete_reason = "expansion_cap"
    segments = sorted(
        (segment for rows in partitions.values() for segment in rows),
        key=lambda row: (float(row["total_cost"]), _text(row["segment_id"])),
    )
    complete = not incomplete_reason and len(searches) == len(orientations) and all(
        bool(search["queue_exhausted"]) for search in searches
    )
    return {
        "schema_version": "core_memory.causal_segment_frontier.v1",
        "segments": segments,
        "complete": complete,
        "termination_reason": "exhausted" if complete else (incomplete_reason or "expansion_cap"),
        "expansions": sum(int(search["expansions"]) for search in searches),
        "partitions_seen": len(partition_keys_seen),
        "direction": requested_direction,
        "junction_density": dict(derived.get("density") or {}),
    }


def root_cause_trace(
    root: Path,
    anchor_ids: list[str],
    *,
    query: str,
    hints: dict | None = None,
    myelination_bonus: dict[str, float] | None = None,
    max_depth: int = 6,
    max_paths: int = 20,
    max_causes: int = 8,
    beam_width: int = 8,
    temporal_frame: str = "auto",
    include_flow: bool = True,
    allowed_source_ids: list[str] | None = None,
    denied_source_ids: list[str] | None = None,
) -> dict:
    root = Path(root)
    index = _read_index(root)
    beads = {str(k): v for k, v in (index.get("beads") or {}).items() if isinstance(v, dict)}
    normalized_hints = normalize_causal_hints(hints)
    if temporal_frame == "auto":
        temporal_frame = normalized_hints.get("temporal_frame") or "auto"
    if temporal_frame == "auto":
        temporal_frame = "historical" if re.search(r"\b(did|was|were|chose|chosen|decided|happened)\b", query.lower()) else "current_truth"
    query_tokens = _tokens(query)
    hint_tokens = _tokens(" ".join(normalized_hints.get("keywords") or []) + " " + " ".join(normalized_hints.get("entities") or []))
    edges = _build_edges(root, index)
    myelination = dict(myelination_bonus or {})
    allowed = {_text(value) for value in allowed_source_ids or [] if _text(value)}
    denied = {_text(value) for value in denied_source_ids or [] if _text(value)}

    anchors = [
        a
        for a in [*_clean_list(anchor_ids), *normalized_hints.get("anchor_ids", [])]
        if _text(a) in beads
        and _source_scope_allows(
            beads[_text(a)],
            allowed_source_ids=allowed,
            denied_source_ids=denied,
        )
    ]
    anchors = list(dict.fromkeys(_text(a) for a in anchors if _text(a)))
    expansion_cap = max(64, max_paths * max(2, beam_width) * max(1, max_depth))
    search = _parameterized_best_first_search(
        beads=beads,
        edges=edges,
        anchors=anchors,
        terminal_set=None,
        length_cap=max_depth,
        direction="upstream",
        drag_enabled=True,
        result_mode="best",
        query_tokens=query_tokens,
        hint_tokens=hint_tokens,
        hints=normalized_hints,
        myelination_bonus=myelination,
        temporal_frame=temporal_frame,
        max_results=max_paths,
        beam_width=beam_width,
        max_expansions=expansion_cap,
        allowed_source_ids=allowed,
        denied_source_ids=denied,
    )
    paths: list[dict[str, Any]] = []
    for result in search["results"]:
        path = _path_record(
            f"path_{len(paths) + 1}",
            result["anchor"],
            result["nodes"],
            result["hops"],
            beads,
            result["total_cost"],
        )
        path["max_depth_reached"] = bool(result["max_depth_reached"])
        paths.append(path)
    warnings = search["warnings"]
    expansions = int(search["expansions"])

    paths = sorted(paths, key=lambda p: (float(p.get("total_cost") or 0.0), -float(p.get("current_truth_confidence") or 0.0), _text(p.get("path_id"))))[: max(1, int(max_paths))]
    root_causes, influence_breakdown = _rank_influence(paths, beads, max_causes=max_causes) if include_flow else ([], [])
    package = _trace_package(paths, beads, temporal_frame)

    return {
        "schema_version": "core_memory.root_cause_attribution.v1",
        "mode": "upstream_causal",
        "anchor_ids": anchors,
        "root_causes": root_causes,
        "causal_paths": paths,
        "trace_package": package,
        "influence_breakdown": influence_breakdown,
        "warnings": warnings,
        "diagnostics": {
            "edge_count": int(len(edges)),
            "path_count": int(len(paths)),
            "expansions": int(expansions),
            "beam_width": int(beam_width),
            "max_depth": int(max_depth),
            "max_paths": int(max_paths),
            "temporal_frame": temporal_frame,
            "semantic_drag": "lexical_token_overlap",
            "myelination_edges": int(len(myelination)),
            "source_scope_applied": bool(allowed or denied),
        },
    }
