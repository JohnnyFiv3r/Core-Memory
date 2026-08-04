"""Build the durable junction roadmap from observed causal segments.

The roadmap is a derived projection. It may sample, search, deduplicate, and
cache structural facts, but it never authors associations or semantic meaning.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from core_memory.graph import root_cause as causal_graph
from core_memory.persistence.junction_roadmap import (
    JUNCTION_ROADMAP_SCHEMA,
    write_junction_roadmap,
)
from core_memory.persistence.myelination_manifest import (
    myelination_manifest_path,
    read_myelination_edge_bonus_map,
)
from core_memory.schema.normalization import normalize_relation_type

DEFAULT_MAX_VERTICES = 200
DEFAULT_NEIGHBORS_PER_VERTEX = 8
DEFAULT_MAX_SEGMENT_LEN = 6
DEFAULT_MAX_EXPANSIONS_PER_PAIR = 5_000
DEFAULT_MAX_PARTITIONS = 32
DEFAULT_MAX_RESULTS_PER_PARTITION = 2
DEFAULT_SOFT_ALTERNATIVES_PER_PAIR = 64
DEFAULT_HARD_ALTERNATIVES_PER_PAIR = 128

_GENERIC_LABEL_TOKENS = {
    "company",
    "context",
    "document",
    "memory",
    "record",
    "source",
    "team",
    "workspace",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_index(root: Path) -> dict[str, Any]:
    try:
        payload = json.loads((root / ".beads" / "index.json").read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return "missing"


def roadmap_input_revision(root: str | Path) -> str:
    root_path = Path(root)
    payload = {
        "index": _file_digest(root_path / ".beads" / "index.json"),
        "myelination": _file_digest(myelination_manifest_path(root_path)),
        "semantic": _file_digest(root_path / ".beads" / "semantic" / "manifest.json"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _label_quality(label: str) -> float:
    tokens = [token for token in "".join(char if char.isalnum() else " " for char in label.casefold()).split() if token]
    if not tokens:
        return 0.0
    informative = [token for token in tokens if token not in _GENERIC_LABEL_TOKENS]
    specificity = min(1.0, len(set(informative)) / 4.0)
    length_quality = min(1.0, len(label.strip()) / 48.0)
    return round((specificity * 0.75) + (length_quality * 0.25), 6)


def _sampling_score(identity: Mapping[str, Any]) -> float:
    tier = str(identity.get("tier") or "")
    support = max(1, int(identity.get("support") or len(identity.get("bead_ids") or [])))
    tier_weight = {
        "goal": 4.0,
        "claim_slot": 3.0,
        "entity_worldline": 2.0,
    }.get(tier, 1.0)
    return round(tier_weight * math.log1p(support) * (1.0 + _label_quality(str(identity.get("label") or ""))), 6)


def sample_junction_identities(
    projection: Mapping[str, Any],
    *,
    max_vertices: int = DEFAULT_MAX_VERTICES,
) -> list[dict[str, Any]]:
    """Select high-support, high-quality identities while retaining every goal."""

    identities: list[dict[str, Any]] = []
    for raw in projection.get("identities") or []:
        if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
            continue
        row = dict(raw)
        row["sampling_score"] = _sampling_score(row)
        identities.append(row)
    goals = sorted(
        (row for row in identities if str(row.get("tier") or "") == "goal"),
        key=lambda row: (-float(row["sampling_score"]), str(row.get("id") or "")),
    )
    others = sorted(
        (row for row in identities if str(row.get("tier") or "") != "goal"),
        key=lambda row: (-float(row["sampling_score"]), str(row.get("id") or "")),
    )
    remaining = max(0, max(1, int(max_vertices)) - len(goals))
    selected = [*goals, *others[:remaining]]
    return [
        {
            "id": str(row.get("id") or ""),
            "tier": str(row.get("tier") or ""),
            "key": str(row.get("key") or ""),
            "label": str(row.get("label") or ""),
            "bead_ids": list(dict.fromkeys(str(value) for value in row.get("bead_ids") or [] if str(value))),
            "support": int(row.get("support") or len(row.get("bead_ids") or [])),
            "sampling_score": float(row["sampling_score"]),
        }
        for row in selected
    ]


def _downstream_distances(
    start_bead_ids: Iterable[str],
    *,
    edges: list[dict[str, Any]],
    max_len: int,
) -> dict[str, int]:
    distances = {str(bead_id): 0 for bead_id in start_bead_ids if str(bead_id)}
    queue = deque(sorted(distances))
    while queue:
        bead_id = queue.popleft()
        depth = distances[bead_id]
        if depth >= max_len:
            continue
        for _edge, neighbor_id in causal_graph._downstream_edges(bead_id, edges):
            neighbor = str(neighbor_id or "")
            if not neighbor or neighbor in distances:
                continue
            distances[neighbor] = depth + 1
            queue.append(neighbor)
    return distances


def _candidate_pairs(
    vertices: list[dict[str, Any]],
    *,
    graph_edges: list[dict[str, Any]],
    max_len: int,
    radius: int,
) -> list[tuple[dict[str, Any], dict[str, Any], int]]:
    """Return directed identity pairs joined by a bounded observed path.

    ``radius`` is the maximum number of observed-hop-nearest destination
    identities retained per source identity. This prevents an O(V^2) frontier
    search while keeping candidate generation grounded in real graph reachability.
    """

    pairs: list[tuple[dict[str, Any], dict[str, Any], int]] = []
    neighbor_limit = max(1, int(radius))
    for start in vertices:
        distances = _downstream_distances(
            start.get("bead_ids") or [],
            edges=graph_edges,
            max_len=max_len,
        )
        near: list[tuple[int, float, str, dict[str, Any]]] = []
        start_id = str(start.get("id") or "")
        for end in vertices:
            end_id = str(end.get("id") or "")
            if not end_id or end_id == start_id:
                continue
            positive_distances = [
                distances[bead_id]
                for bead_id in end.get("bead_ids") or []
                if bead_id in distances and distances[bead_id] > 0
            ]
            if not positive_distances:
                continue
            near.append(
                (
                    min(positive_distances),
                    -float(end.get("sampling_score") or 0.0),
                    end_id,
                    end,
                )
            )
        for distance, _score, _end_id, end in sorted(near)[:neighbor_limit]:
            pairs.append((start, end, distance))
    return pairs


def _stable_refs(values: Iterable[Any]) -> list[str]:
    refs = {causal_graph._stable_reference(value) for value in values}
    refs.discard("")
    return sorted(refs)


def edge_cost_row(
    edge: Mapping[str, Any],
    *,
    beads: Mapping[str, Mapping[str, Any]],
    myelination_bonus: Mapping[str, float],
) -> dict[str, Any]:
    """Split one observed edge into cached components and dynamic references."""

    breakdown = dict(edge.get("cost_breakdown") or {})
    relation = normalize_relation_type(str(edge.get("relation") or ""))
    raw_src = str(edge.get("raw_src") or "")
    raw_dst = str(edge.get("raw_dst") or "")
    bonus_key = f"{raw_src}|{relation}|{raw_dst}"
    bonus = float(myelination_bonus.get(bonus_key) or 0.0)
    from_id = str(edge.get("from") or "")
    to_id = str(edge.get("to") or "")
    endpoint_rows = [beads.get(from_id) or {}, beads.get(to_id) or {}]
    source_ids = sorted(
        {
            value
            for bead in endpoint_rows
            for value in causal_graph._source_tokens(bead)
            if value
        }
    )
    claim_refs = sorted(
        {
            value
            for bead in endpoint_rows
            for value in causal_graph._claim_refs(bead)
            if value
        }
    )
    temporal_refs = []
    for bead_id, bead in ((from_id, endpoint_rows[0]), (to_id, endpoint_rows[1])):
        timestamp, field = causal_graph.timestamp_for_bead(dict(bead))
        temporal_refs.append({"bead_id": bead_id, "timestamp": timestamp or None, "field": field or None})
    contradiction_refs = []
    if relation in causal_graph.CONFLICT_RELATIONS or breakdown.get("conflict_flags"):
        contradiction_refs.append(str(edge.get("edge_id") or ""))
    cached_components = {
        "structural": round(float(breakdown.get("relation_prior_cost") or 0.0), 6),
        "confidence": round(float(breakdown.get("confidence_penalty") or 0.0), 6),
        "myelination": round(-bonus, 6),
        "evidence": round(-float(breakdown.get("evidence_bonus") or 0.0), 6),
        "validation": round(-float(breakdown.get("user_validation_bonus") or 0.0), 6),
    }
    cached_raw = sum(cached_components.values())
    return {
        "edge_id": str(edge.get("edge_id") or ""),
        "from_bead_id": from_id,
        "to_bead_id": to_id,
        "raw_source_bead_id": raw_src,
        "raw_target_bead_id": raw_dst,
        "relationship": relation,
        "cached_components": cached_components,
        "cached_raw_cost": round(cached_raw, 6),
        "cached_floor_cost": round(max(0.001, cached_raw), 6),
        "dynamic_refs": {
            "source_ids": source_ids,
            "claim_refs": claim_refs,
            "temporal_refs": temporal_refs,
            "contradiction_refs": [value for value in contradiction_refs if value],
            "evidence_refs": _stable_refs(breakdown.get("evidence_refs") or []),
        },
    }


def _alternative_metrics(alternative: Mapping[str, Any]) -> dict[str, float | int]:
    rows = [row for row in alternative.get("edge_cost_rows") or [] if isinstance(row, dict)]
    component_totals = {
        name: round(
            sum(float((row.get("cached_components") or {}).get(name) or 0.0) for row in rows),
            6,
        )
        for name in ("structural", "confidence", "myelination", "evidence", "validation")
    }
    signature = dict(alternative.get("dynamic_cost_signature") or {})
    return {
        **component_totals,
        "evidence_quality": len(signature.get("evidence_refs") or []),
        "validation_quality": round(-component_totals["validation"], 6),
    }


def _dominates(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_metrics = _alternative_metrics(left)
    right_metrics = _alternative_metrics(right)
    cost_names = ("structural", "confidence", "myelination", "evidence", "validation")
    weakly_better = all(float(left_metrics[name]) <= float(right_metrics[name]) for name in cost_names)
    weakly_better = weakly_better and int(left_metrics["evidence_quality"]) >= int(right_metrics["evidence_quality"])
    weakly_better = weakly_better and float(left_metrics["validation_quality"]) >= float(
        right_metrics["validation_quality"]
    )
    strictly_better = any(float(left_metrics[name]) < float(right_metrics[name]) for name in cost_names)
    strictly_better = strictly_better or int(left_metrics["evidence_quality"]) > int(right_metrics["evidence_quality"])
    strictly_better = strictly_better or float(left_metrics["validation_quality"]) > float(
        right_metrics["validation_quality"]
    )
    return weakly_better and strictly_better


def _alternative_sort_key(alternative: Mapping[str, Any]) -> tuple[Any, ...]:
    rows = [row for row in alternative.get("edge_cost_rows") or [] if isinstance(row, dict)]
    cached_floor = sum(float(row.get("cached_floor_cost") or 0.0) for row in rows)
    metrics = _alternative_metrics(alternative)
    return (
        round(cached_floor, 6),
        -int(metrics["evidence_quality"]),
        -float(metrics["validation_quality"]),
        str(alternative.get("segment_id") or ""),
    )


def retain_nondominated_alternatives(
    alternatives: Iterable[dict[str, Any]],
    *,
    soft_limit: int = DEFAULT_SOFT_ALTERNATIVES_PER_PAIR,
    hard_limit: int = DEFAULT_HARD_ALTERNATIVES_PER_PAIR,
) -> dict[str, Any]:
    """Retain per-partition Pareto fronts without dropping mandatory partitions."""

    partitions: dict[str, list[dict[str, Any]]] = {}
    for candidate in alternatives:
        signature = dict(candidate.get("dynamic_cost_signature") or {})
        partition_key = str(signature.get("partition_key") or "")
        current = partitions.setdefault(partition_key, [])
        if any(_dominates(existing, candidate) for existing in current):
            continue
        current[:] = [existing for existing in current if not _dominates(candidate, existing)]
        if not any(existing.get("segment_id") == candidate.get("segment_id") for existing in current):
            current.append(candidate)
            current.sort(key=_alternative_sort_key)

    mandatory = [rows[0] for _key, rows in sorted(partitions.items()) if rows]
    if len(mandatory) > max(1, int(hard_limit)):
        return {
            "ok": False,
            "reason": "mandatory_partition_representatives_exceed_hard_limit",
            "partition_count": len(mandatory),
            "alternatives": [],
        }
    target = max(max(1, int(soft_limit)), len(mandatory))
    target = min(max(1, int(hard_limit)), target)
    retained = list(mandatory)
    mandatory_ids = {str(row.get("segment_id") or "") for row in mandatory}
    optional = sorted(
        (
            row
            for rows in partitions.values()
            for row in rows
            if str(row.get("segment_id") or "") not in mandatory_ids
        ),
        key=_alternative_sort_key,
    )
    retained.extend(optional[: max(0, target - len(retained))])
    retained.sort(key=_alternative_sort_key)
    return {
        "ok": True,
        "partition_count": len(partitions),
        "truncated": len(retained) < sum(len(rows) for rows in partitions.values()),
        "alternatives": retained,
    }


def _roadmap_alternative(
    segment: Mapping[str, Any],
    *,
    beads: Mapping[str, Mapping[str, Any]],
    myelination_bonus: Mapping[str, float],
) -> dict[str, Any]:
    rows = [
        edge_cost_row(edge, beads=beads, myelination_bonus=myelination_bonus)
        for edge in segment.get("edges") or []
        if isinstance(edge, dict)
    ]
    return {
        "segment_id": str(segment.get("segment_id") or ""),
        "direction": str(segment.get("direction") or "downstream"),
        "bead_ids": list(segment.get("bead_ids") or []),
        "edge_cost_rows": rows,
        "dynamic_cost_signature": dict(segment.get("dynamic_cost_signature") or {}),
        "cached_floor_subtotal": round(sum(float(row["cached_floor_cost"]) for row in rows), 6),
        "build_diagnostics": {
            "phase_2_total_cost": float(segment.get("total_cost") or 0.0),
            "phase_2_confidence": float(segment.get("confidence") or 0.0),
        },
    }


def _roadmap_edge_id(start_id: str, end_id: str) -> str:
    seed = f"{start_id}\n{end_id}"
    return f"roadmap-edge:{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:24]}"


def build_junction_roadmap(
    root: str | Path,
    *,
    projection: Mapping[str, Any],
    max_vertices: int = DEFAULT_MAX_VERTICES,
    radius: int = DEFAULT_NEIGHBORS_PER_VERTEX,
    max_len: int = DEFAULT_MAX_SEGMENT_LEN,
    max_expansions_per_pair: int = DEFAULT_MAX_EXPANSIONS_PER_PAIR,
    max_partitions: int = DEFAULT_MAX_PARTITIONS,
    max_results_per_partition: int = DEFAULT_MAX_RESULTS_PER_PARTITION,
    soft_alternatives_per_pair: int = DEFAULT_SOFT_ALTERNATIVES_PER_PAIR,
    hard_alternatives_per_pair: int = DEFAULT_HARD_ALTERNATIVES_PER_PAIR,
    persist: bool = True,
) -> dict[str, Any]:
    """Build and optionally persist a complete-frontier junction roadmap."""

    started = time.perf_counter()
    root_path = Path(root)
    input_revision = roadmap_input_revision(root_path)
    vertices = sample_junction_identities(projection, max_vertices=max_vertices)
    index = _read_index(root_path)
    beads = {
        str(bead_id): bead
        for bead_id, bead in (index.get("beads") or {}).items()
        if isinstance(bead, dict)
    }
    graph_edges = causal_graph._build_edges(root_path, index)
    myelination_bonus = read_myelination_edge_bonus_map(root_path)
    density = dict(projection.get("density") or {})
    calibration = dict(projection.get("metadata") or {})
    limitations = list(projection.get("limitations") or [])
    roadmap_edges: list[dict[str, Any]] = []
    omitted_pairs: list[dict[str, Any]] = []
    omission_reasons: Counter[str] = Counter()
    candidate_pairs: list[tuple[dict[str, Any], dict[str, Any], int]] = []

    if bool(density.get("stitching_ready")):
        candidate_pairs = _candidate_pairs(
            vertices,
            graph_edges=graph_edges,
            max_len=max(1, int(max_len)),
            radius=max(1, int(radius)),
        )
        for start, end, observed_hops in candidate_pairs:
            start_id = str(start.get("id") or "")
            end_id = str(end.get("id") or "")
            frontier = causal_graph.segment_frontier_between(
                root_path,
                start_id,
                end_id,
                max_len=max(1, int(max_len)),
                direction="downstream",
                temporal_frame="current_truth",
                max_expansions=max(1, int(max_expansions_per_pair)),
                max_partitions=max(1, int(max_partitions)),
                max_results_per_partition=max(1, int(max_results_per_partition)),
                projection=projection,
            )
            if not bool(frontier.get("complete")):
                reason = str(frontier.get("termination_reason") or "incomplete_frontier")
                omission_reasons[reason] += 1
                omitted_pairs.append(
                    {
                        "start_junction_id": start_id,
                        "end_junction_id": end_id,
                        "reason": reason,
                        "expansions": int(frontier.get("expansions") or 0),
                        "partitions_seen": int(frontier.get("partitions_seen") or 0),
                    }
                )
                continue
            alternatives = [
                _roadmap_alternative(
                    segment,
                    beads=beads,
                    myelination_bonus=myelination_bonus,
                )
                for segment in frontier.get("segments") or []
                if isinstance(segment, dict)
            ]
            retained = retain_nondominated_alternatives(
                alternatives,
                soft_limit=soft_alternatives_per_pair,
                hard_limit=hard_alternatives_per_pair,
            )
            if not bool(retained.get("ok")):
                reason = str(retained.get("reason") or "frontier_resource_limit")
                omission_reasons[reason] += 1
                omitted_pairs.append(
                    {
                        "start_junction_id": start_id,
                        "end_junction_id": end_id,
                        "reason": reason,
                        "partitions_seen": int(retained.get("partition_count") or 0),
                    }
                )
                continue
            kept = list(retained.get("alternatives") or [])
            if not kept:
                continue
            roadmap_edges.append(
                {
                    "roadmap_edge_id": _roadmap_edge_id(start_id, end_id),
                    "start_junction_id": start_id,
                    "end_junction_id": end_id,
                    "observed_hop_distance": observed_hops,
                    "partition_count": int(retained.get("partition_count") or 0),
                    "frontier_truncated": bool(retained.get("truncated")),
                    "alternatives": kept,
                }
            )
    else:
        limitations.append("junction_density_gate_failed_roadmap_build_deferred")

    ending_revision = roadmap_input_revision(root_path)
    if ending_revision != input_revision:
        return {
            "ok": False,
            "schema_version": JUNCTION_ROADMAP_SCHEMA,
            "status": "stale_build",
            "error": "roadmap_inputs_changed_during_build",
            "input_revision": input_revision,
            "ending_revision": ending_revision,
            "persisted": False,
        }
    built_at = _utc_now()
    alternative_count = sum(len(edge.get("alternatives") or []) for edge in roadmap_edges)
    status = "ready" if bool(density.get("stitching_ready")) else "deferred"
    roadmap = {
        "schema_version": JUNCTION_ROADMAP_SCHEMA,
        "status": status,
        "built_at": built_at,
        "input_revision": input_revision,
        "vertices": vertices,
        "edges": roadmap_edges,
        "roadmap_meta": {
            "built_at": built_at,
            "input_revision": input_revision,
            "vertex_count": len(vertices),
            "edge_count": len(roadmap_edges),
            "alternative_count": alternative_count,
            "candidate_pair_count": len(candidate_pairs),
            "omitted_pair_count": len(omitted_pairs),
            "omission_reasons": dict(sorted(omission_reasons.items())),
            "omitted_pairs": omitted_pairs[:500],
            "omitted_pair_receipts_truncated": len(omitted_pairs) > 500,
            "d_p": calibration.get("d_p"),
            "d_p_source": calibration.get("d_p_source"),
            "junction_density": density,
            "myelination_edge_count": len(myelination_bonus),
            "build_duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "config": {
                "max_vertices": max(1, int(max_vertices)),
                "radius": max(1, int(radius)),
                "radius_mode": "bounded_observed_hop_neighbors",
                "max_len": max(1, int(max_len)),
                "max_expansions_per_pair": max(1, int(max_expansions_per_pair)),
                "max_partitions": max(1, int(max_partitions)),
                "max_results_per_partition": max(1, int(max_results_per_partition)),
                "soft_alternatives_per_pair": max(1, int(soft_alternatives_per_pair)),
                "hard_alternatives_per_pair": max(1, int(hard_alternatives_per_pair)),
            },
        },
        "limitations": sorted(set(str(value) for value in limitations if str(value))),
    }
    manifest_path = None
    if persist:
        manifest_path = write_junction_roadmap(root_path, roadmap)
    return {
        "ok": True,
        "present": bool(persist),
        "persisted": bool(persist),
        "manifest_path": str(manifest_path) if manifest_path else None,
        **roadmap,
    }


__all__ = [
    "DEFAULT_HARD_ALTERNATIVES_PER_PAIR",
    "DEFAULT_MAX_EXPANSIONS_PER_PAIR",
    "DEFAULT_MAX_PARTITIONS",
    "DEFAULT_MAX_RESULTS_PER_PARTITION",
    "DEFAULT_MAX_SEGMENT_LEN",
    "DEFAULT_MAX_VERTICES",
    "DEFAULT_NEIGHBORS_PER_VERTEX",
    "DEFAULT_SOFT_ALTERNATIVES_PER_PAIR",
    "build_junction_roadmap",
    "edge_cost_row",
    "retain_nondominated_alternatives",
    "roadmap_input_revision",
    "sample_junction_identities",
]
