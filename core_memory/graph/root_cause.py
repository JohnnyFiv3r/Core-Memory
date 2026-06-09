from __future__ import annotations

import heapq
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from core_memory.schema.normalization import normalize_relation_type


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
UPSTREAM_FROM_TARGET = {"causes", "led_to", "enabled", "enables", "unblocks", "supports", "reinforces", "resolves", "diagnoses"}
BIDIRECTIONAL_WEAK = {"associated_with", "related_to", "shared_entity", "refines", "applies_pattern_of", "mirrors"}
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
        if status in {"retracted", "inactive"}:
            continue
        _add_edge(
            edges,
            assoc.get("source_bead") or assoc.get("source_bead_id"),
            assoc.get("target_bead") or assoc.get("target_bead_id"),
            assoc.get("relationship") or assoc.get("rel"),
            source="association",
            confidence=assoc.get("confidence"),
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
    rel = normalize_relation_type(rel)
    if rel in {"caused_by", "causes", "led_to"}:
        return "causal"
    if rel in {"enabled", "enables", "unblocks", "blocked_by", "blocks_unblocks"}:
        return "influence"
    if rel in {"supports", "derived_from", "documented_by", "informed_by", "resolves", "diagnoses"}:
        return "evidence"
    if rel in CONFLICT_RELATIONS:
        return "conflict"
    return "related"


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
) -> tuple[float, dict[str, Any]]:
    rel = normalize_relation_type(_text(edge.get("rel")))
    family = _relation_family(rel)
    semantic_score = _semantic_relevance(query_tokens, hint_tokens, cause)
    semantic_floor = 0.35
    semantic_penalty = 0.25 * max(0.0, semantic_floor - semantic_score)
    confidence = _coerce_confidence(edge.get("confidence"), 0.75)
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
        + (1.0 - confidence)
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
        "confidence_penalty": round(1.0 - confidence, 6),
        "temporal_penalty": round(temporal_cost, 6),
        "claim_state_penalty": round(claim_cost, 6),
        "contradiction_penalty": round(contradiction, 6),
        "evidence_gap_penalty": round(evidence_gap, 6),
        "semantic_relevance_score": round(semantic_score, 6),
        "semantic_similarity_floor": semantic_floor,
        "semantic_mismatch_penalty": round(semantic_penalty, 6),
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
    myelination_bonus: dict[str, float] | None = None,
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
    myelination: dict[str, float] = dict(myelination_bonus or {})

    anchors = [a for a in [*_clean_list(anchor_ids), *normalized_hints.get("anchor_ids", [])] if _text(a) in beads]
    anchors = list(dict.fromkeys(_text(a) for a in anchors if _text(a)))
    heap: list[tuple[float, int, str, str, list[str], list[dict[str, Any]]]] = []
    counter = 0
    for anchor in anchors:
        counter += 1
        heapq.heappush(heap, (0.0, counter, anchor, anchor, [anchor], []))

    paths: list[dict[str, Any]] = []
    seen_path: set[tuple[str, ...]] = set()
    warnings: list[dict[str, str]] = []
    expansions = 0
    expansion_cap = max(64, max_paths * max(2, beam_width) * max(1, max_depth))

    while heap and len(paths) < max(1, int(max_paths)) and expansions < expansion_cap:
        cost, _, anchor, node, nodes, hops = heapq.heappop(heap)
        expansions += 1
        if hops:
            sig = tuple(nodes)
            if sig not in seen_path:
                seen_path.add(sig)
                paths.append(_path_record(f"path_{len(paths) + 1}", anchor, nodes, hops, beads, cost))
        if len(hops) >= max(1, int(max_depth)):
            if paths:
                paths[-1]["max_depth_reached"] = True
            continue
        candidates = _upstream_edges(node, edges)
        if not candidates and not hops:
            warnings.append({"kind": "no_upstream_edges", "message": f"No upstream causal edges found for anchor {node}."})
            continue
        ranked_next = []
        for edge, parent in candidates:
            if parent in nodes:
                continue
            parent_bead = beads.get(parent)
            node_bead = beads.get(node)
            if not isinstance(parent_bead, dict) or not isinstance(node_bead, dict):
                continue
            step_cost, breakdown = _edge_cost(
                edge,
                effect=node_bead,
                cause=parent_bead,
                query_tokens=query_tokens,
                hint_tokens=hint_tokens,
                hints=normalized_hints,
                myelination_bonus=myelination,
                temporal_frame=temporal_frame,
            )
            hop = {
                "from": parent,
                "to": node,
                "raw_src": edge.get("src"),
                "raw_dst": edge.get("dst"),
                "edge_id": edge.get("edge_id"),
                "relation": normalize_relation_type(_text(edge.get("rel"))),
                "normalized_direction": "upstream",
                "cost": round(step_cost, 6),
                "confidence": breakdown.get("confidence"),
                "candidate_bead_semantic_relevance_score": breakdown.get("semantic_relevance_score"),
                "semantic_mismatch_penalty": breakdown.get("semantic_mismatch_penalty"),
                "myelination_bonus": breakdown.get("myelination_bonus"),
                "evidence_refs": breakdown.get("evidence_refs") or [],
                "cost_breakdown": breakdown,
            }
            ranked_next.append((cost + step_cost, parent, nodes + [parent], hops + [hop]))
        ranked_next.sort(key=lambda row: (row[0], row[1]))
        for next_cost, parent, next_nodes, next_hops in ranked_next[: max(1, int(beam_width))]:
            counter += 1
            heapq.heappush(heap, (next_cost, counter, anchor, parent, next_nodes, next_hops))

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
        },
    }
