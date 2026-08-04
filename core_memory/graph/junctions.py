"""Claims-first junction identities for PER and roadmap retrieval.

This module is a read-side projection. It derives recurring memory locations
from canonical claims, curated entity worldlines, goals, and cached semantic
vectors. It never writes associations or semantic meaning.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from core_memory.entity.quality import is_meaningful_entity_label
from core_memory.graph.worldlines import derive_worldlines
from core_memory.persistence.store_claim_ops import read_all_claim_rows

JUNCTION_SCHEMA = "core_memory.junction_projection.v1"
CALIBRATION_FRACTION = 0.5

_TIER_ORDER = {
    "exact_bead": 1,
    "claim_slot": 2,
    "goal": 3,
    "entity_worldline": 3,
    "embedding": 4,
}


def _read_index(root: Path) -> dict[str, Any]:
    try:
        payload = json.loads((root / ".beads" / "index.json").read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _identity_id(tier: str, *parts: str) -> str:
    encoded = ":".join(quote(_normalized(part), safe="-._~") for part in parts)
    return f"{tier}:{encoded}"


def _quantile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(1.0, float(fraction))) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _distribution(values: Sequence[float]) -> dict[str, float | int]:
    rows = [float(value) for value in values]
    if not rows:
        return {
            "count": 0,
            "min": 0.0,
            "p25": 0.0,
            "p50": 0.0,
            "p75": 0.0,
            "max": 0.0,
            "mean": 0.0,
        }
    return {
        "count": len(rows),
        "min": min(rows),
        "p25": _quantile(rows, 0.25),
        "p50": _quantile(rows, 0.50),
        "p75": _quantile(rows, 0.75),
        "max": max(rows),
        "mean": sum(rows) / len(rows),
    }


def _cosine_distance(left: Sequence[float], right: Sequence[float]) -> float | None:
    if not left or len(left) != len(right):
        return None
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return None
    similarity = max(-1.0, min(1.0, dot / (left_norm * right_norm)))
    return max(0.0, min(2.0, 1.0 - similarity))


def _adjacent_pairs(worldlines: Sequence[dict[str, Any]]) -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for worldline in worldlines:
        bead_ids = [str(bead_id) for bead_id in (worldline.get("bead_ids") or []) if str(bead_id)]
        for left, right in zip(bead_ids, bead_ids[1:]):
            if left and right and left != right:
                pairs.add((left, right))
    return sorted(pairs)


def _calibrate_embedding_radius(
    worldlines: Sequence[dict[str, Any]],
    embeddings: Mapping[str, Sequence[float]],
) -> dict[str, Any]:
    distances: list[float] = []
    for left_id, right_id in _adjacent_pairs(worldlines):
        distance = _cosine_distance(embeddings.get(left_id, ()), embeddings.get(right_id, ()))
        if distance is not None:
            distances.append(distance)
    if not distances:
        return {
            "d_p": None,
            "d_p_source": "unavailable_no_cached_backbone_adjacency",
            "calibration_fraction": CALIBRATION_FRACTION,
            "adjacent_pair_count": 0,
            "mean_adjacent_distance": None,
            "adjacent_distance_p25": None,
            "adjacent_distance_p50": None,
            "adjacent_distance_p75": None,
        }
    mean_distance = sum(distances) / len(distances)
    return {
        "d_p": mean_distance * CALIBRATION_FRACTION,
        "d_p_source": "calibrated_from_backbone_adjacency",
        "calibration_fraction": CALIBRATION_FRACTION,
        "adjacent_pair_count": len(distances),
        "mean_adjacent_distance": mean_distance,
        "adjacent_distance_p25": _quantile(distances, 0.25),
        "adjacent_distance_p50": _quantile(distances, 0.50),
        "adjacent_distance_p75": _quantile(distances, 0.75),
    }


def _embedding_neighbors(
    embeddings: Mapping[str, Sequence[float]],
    radius: float | None,
) -> dict[str, set[str]]:
    neighbors: dict[str, set[str]] = defaultdict(set)
    if radius is None or radius < 0.0:
        return neighbors
    by_dimension: dict[int, list[tuple[str, Sequence[float]]]] = defaultdict(list)
    for bead_id, vector in embeddings.items():
        if bead_id and vector:
            by_dimension[len(vector)].append((str(bead_id), vector))
    for rows in by_dimension.values():
        vectorized = _vectorized_embedding_neighbors(rows, radius)
        if vectorized is not None:
            for bead_id, bead_neighbors in vectorized.items():
                neighbors[bead_id].update(bead_neighbors)
            continue
        for position, (left_id, left_vector) in enumerate(rows):
            for right_id, right_vector in rows[position + 1 :]:
                distance = _cosine_distance(left_vector, right_vector)
                if distance is not None and distance <= radius:
                    neighbors[left_id].add(right_id)
                    neighbors[right_id].add(left_id)
    return neighbors


def _vectorized_embedding_neighbors(
    rows: Sequence[tuple[str, Sequence[float]]],
    radius: float,
) -> dict[str, set[str]] | None:
    """Use bounded matrix blocks when NumPy is present; otherwise fall back."""

    try:
        import numpy as np  # type: ignore
    except ImportError:
        return None
    if not rows:
        return {}
    try:
        matrix = np.asarray([vector for _bead_id, vector in rows], dtype=float)
        norms = np.linalg.norm(matrix, axis=1)
        valid_positions = np.flatnonzero(norms > 0.0)
        if not len(valid_positions):
            return {}
        normalized = matrix[valid_positions] / norms[valid_positions, None]
        ids = [rows[int(position)][0] for position in valid_positions]
        minimum_similarity = 1.0 - float(radius)
        neighbors: dict[str, set[str]] = defaultdict(set)
        block_size = 256
        for start in range(0, len(ids), block_size):
            similarities = normalized[start : start + block_size] @ normalized.T
            for offset, row_similarities in enumerate(similarities):
                left_position = start + offset
                for right_position in np.flatnonzero(row_similarities >= minimum_similarity):
                    right_position = int(right_position)
                    if right_position <= left_position:
                        continue
                    left_id = ids[left_position]
                    right_id = ids[right_position]
                    neighbors[left_id].add(right_id)
                    neighbors[right_id].add(left_id)
        return neighbors
    except (TypeError, ValueError):
        return None


def _claim_identities(root: Path, beads: Mapping[str, Any]) -> list[dict[str, Any]]:
    claims, _updates = read_all_claim_rows(str(root))
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        subject = str(claim.get("subject") or "").strip()
        slot = str(claim.get("slot") or "").strip()
        bead_id = str(claim.get("source_bead_id") or "").strip()
        key = (_normalized(subject), _normalized(slot))
        if not key[0] or not key[1] or not bead_id or bead_id not in beads:
            continue
        row = grouped.setdefault(
            key,
            {
                "id": _identity_id("claim", subject, slot),
                "tier": "claim_slot",
                "key": f"{subject}/{slot}",
                "label": f"{subject} · {slot}",
                "bead_ids": [],
            },
        )
        if bead_id not in row["bead_ids"]:
            row["bead_ids"].append(bead_id)
    return list(grouped.values())


def _entity_support_bounds(
    entity_worldlines: Sequence[dict[str, Any]],
    total_beads: int,
) -> dict[str, Any]:
    supports = [
        len(set(str(value) for value in (row.get("bead_ids") or []) if str(value)))
        for row in entity_worldlines
    ]
    if not supports:
        return {
            "support_floor": 0,
            "support_q25": 0.0,
            "support_q50": 0.0,
            "support_q75": 0.0,
            "ubiquity_support_cap": 0,
            "ubiquity_fraction_cap": 0.0,
            "sample_count": 0,
        }
    q25 = _quantile(supports, 0.25)
    q50 = _quantile(supports, 0.50)
    q75 = _quantile(supports, 0.75)
    upper_fence = q75 + 1.5 * max(0.0, q75 - q25)
    support_floor = max(1, int(math.ceil(q25)))
    ubiquity_cap = max(support_floor, min(max(1, total_beads), int(math.ceil(upper_fence))))
    return {
        "support_floor": support_floor,
        "support_q25": q25,
        "support_q50": q50,
        "support_q75": q75,
        "ubiquity_support_cap": ubiquity_cap,
        "ubiquity_fraction_cap": ubiquity_cap / max(1, total_beads),
        "sample_count": len(supports),
    }


def _is_curated_entity_worldline(row: Mapping[str, Any]) -> bool:
    return str(row.get("kind") or "") == "entity" and is_meaningful_entity_label(
        str(row.get("label") or row.get("key") or "")
    )


def _worldline_identities(
    worldlines: Sequence[dict[str, Any]],
    *,
    total_beads: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    entity_worldlines = [
        row
        for row in worldlines
        if _is_curated_entity_worldline(row)
    ]
    bounds = _entity_support_bounds(entity_worldlines, total_beads)
    identities: list[dict[str, Any]] = []
    for row in worldlines:
        kind = str(row.get("kind") or "")
        bead_ids = list(dict.fromkeys(str(value) for value in (row.get("bead_ids") or []) if str(value)))
        if kind == "entity":
            if not _is_curated_entity_worldline(row):
                continue
            support = len(bead_ids)
            if support < int(bounds["support_floor"]) or support > int(bounds["ubiquity_support_cap"]):
                continue
            identities.append(
                {
                    "id": _identity_id("entity", str(row.get("key") or row.get("id") or "")),
                    "tier": "entity_worldline",
                    "key": str(row.get("key") or row.get("id") or ""),
                    "label": str(row.get("label") or row.get("key") or ""),
                    "bead_ids": bead_ids,
                    "worldline_id": str(row.get("id") or ""),
                }
            )
        elif kind == "goal" and bead_ids:
            goal_id = str(row.get("key") or bead_ids[0])
            identities.append(
                {
                    "id": _identity_id("goal", goal_id),
                    "tier": "goal",
                    "key": goal_id,
                    "label": str(row.get("label") or goal_id),
                    "bead_ids": bead_ids,
                    "worldline_id": str(row.get("id") or ""),
                }
            )
    return identities, bounds


def derive_junction_projection(
    root: str | Path,
    *,
    embeddings: Mapping[str, Sequence[float]] | None = None,
    embedding_source: str | None = None,
    include_beads: bool = True,
) -> dict[str, Any]:
    """Derive junction identities, calibrated fallback radius, and density gate."""

    root_path = Path(root)
    index = _read_index(root_path)
    beads = index.get("beads") if isinstance(index.get("beads"), dict) else {}
    worldlines = list(derive_worldlines(root_path).get("worldlines") or [])

    vector_source = str(embedding_source or ("provided" if embeddings is not None else "unavailable"))
    vectors = dict(embeddings or {})
    vectors = {
        str(bead_id): [float(value) for value in vector]
        for bead_id, vector in vectors.items()
        if str(bead_id) in beads and vector
    }

    calibration = _calibrate_embedding_radius(worldlines, vectors)
    embedding_neighbors = _embedding_neighbors(vectors, calibration.get("d_p"))

    identities = _claim_identities(root_path, beads)
    worldline_identities, entity_bounds = _worldline_identities(worldlines, total_beads=len(beads))
    identities.extend(worldline_identities)
    identities.sort(key=lambda row: (_TIER_ORDER.get(str(row.get("tier") or ""), 99), str(row.get("id") or "")))

    identity_membership: dict[str, set[str]] = defaultdict(set)
    corroborating: dict[str, set[str]] = defaultdict(set)
    for identity in identities:
        members = [str(bead_id) for bead_id in (identity.get("bead_ids") or []) if str(bead_id) in beads]
        identity["bead_ids"] = members
        identity["support"] = len(members)
        identity["junction_set_size"] = len(members)
        identity["corroboration_count"] = max(0, len(members) - 1)
        for bead_id in members:
            identity_membership[bead_id].add(str(identity.get("id") or ""))
            corroborating[bead_id].update(member for member in members if member != bead_id)
    for bead_id, neighbors in embedding_neighbors.items():
        corroborating[bead_id].update(neighbors)

    bead_rows: list[dict[str, Any]] = []
    for bead_id in sorted(beads):
        corroborating_ids = sorted(corroborating.get(str(bead_id), set()))
        embedding_ids = sorted(embedding_neighbors.get(str(bead_id), set()))
        bead_rows.append(
            {
                "bead_id": str(bead_id),
                "junction_identity_ids": sorted(identity_membership.get(str(bead_id), set())),
                "junction_set_size": 1 + len(corroborating_ids),
                "corroboration_count": len(corroborating_ids),
                "corroborating_bead_ids": corroborating_ids,
                "embedding_neighbor_ids": embedding_ids,
            }
        )

    empty_identity_count = sum(1 for identity in identities if int(identity.get("support") or 0) <= 1)
    recurring_identity_count = len(identities) - empty_identity_count
    empty_fraction = empty_identity_count / max(1, len(identities))
    stitching_ready = bool(identities) and empty_fraction <= 0.5
    identity_junction_sizes = [int(identity.get("junction_set_size") or 0) for identity in identities]
    identity_corroboration_counts = [int(identity.get("corroboration_count") or 0) for identity in identities]
    bead_junction_sizes = [int(row.get("junction_set_size") or 0) for row in bead_rows]
    density = {
        "identity_count": len(identities),
        "recurring_identity_count": recurring_identity_count,
        "empty_junction_identity_count": empty_identity_count,
        "empty_junction_identity_fraction": empty_fraction,
        "bead_count": len(beads),
        "beads_with_corroboration_count": sum(1 for row in bead_rows if int(row["corroboration_count"]) > 0),
        "beads_with_corroboration_fraction": (
            sum(1 for row in bead_rows if int(row["corroboration_count"]) > 0) / max(1, len(bead_rows))
        ),
        "identity_junction_set_size_distribution": _distribution(identity_junction_sizes),
        "identity_corroboration_count_distribution": _distribution(identity_corroboration_counts),
        "bead_junction_set_size_distribution": _distribution(bead_junction_sizes),
        "max_empty_junction_identity_fraction": 0.5,
        "stitching_ready": stitching_ready,
        "gate_reason": "junction_density_sufficient" if stitching_ready else "most_junction_identities_are_empty",
    }
    limitations: list[str] = []
    if not vectors:
        limitations.append("cached_embeddings_unavailable_embedding_junctions_disabled")
    if not stitching_ready:
        limitations.append("junction_density_gate_failed_defer_per_search")

    out = {
        "ok": True,
        "schema_version": JUNCTION_SCHEMA,
        "identities": identities,
        "density": density,
        "metadata": {
            **calibration,
            "embedding_source": vector_source,
            "embedding_bead_count": len(vectors),
            "entity_support": entity_bounds,
            "identity_resolution_order": [
                "exact_bead",
                "claim_slot",
                "goal",
                "entity_worldline",
                "embedding",
            ],
        },
        "limitations": limitations,
    }
    if include_beads:
        out["beads"] = bead_rows
    return out


def resolve_junction_matches(
    root: str | Path,
    left_bead_id: str,
    right_bead_id: str,
    *,
    projection: dict[str, Any] | None = None,
    embeddings: Mapping[str, Sequence[float]] | None = None,
) -> dict[str, Any]:
    """Resolve the ordered junction signals shared by two beads."""

    left = str(left_bead_id or "")
    right = str(right_bead_id or "")
    derived = projection or derive_junction_projection(root, embeddings=embeddings, include_beads=True)
    bead_rows = {str(row.get("bead_id") or ""): row for row in (derived.get("beads") or [])}
    identities = {str(row.get("id") or ""): row for row in (derived.get("identities") or [])}
    matches: list[dict[str, Any]] = []
    if left and left == right:
        matches.append({"tier": "exact_bead", "identity_id": f"bead:{left}", "bead_ids": [left]})

    left_ids = set(bead_rows.get(left, {}).get("junction_identity_ids") or [])
    right_ids = set(bead_rows.get(right, {}).get("junction_identity_ids") or [])
    for identity_id in sorted(left_ids & right_ids):
        identity = identities.get(str(identity_id), {})
        matches.append(
            {
                "tier": str(identity.get("tier") or ""),
                "identity_id": str(identity_id),
                "label": str(identity.get("label") or ""),
                "bead_ids": list(identity.get("bead_ids") or []),
            }
        )

    if right in set(bead_rows.get(left, {}).get("embedding_neighbor_ids") or []):
        pair_key = "|".join(sorted((left, right)))
        pair_hash = hashlib.sha256(pair_key.encode("utf-8")).hexdigest()[:16]
        matches.append(
            {
                "tier": "embedding",
                "identity_id": f"embedding:{pair_hash}",
                "bead_ids": [left, right],
            }
        )
    matches.sort(key=lambda row: (_TIER_ORDER.get(str(row.get("tier") or ""), 99), str(row.get("identity_id") or "")))
    return {
        "ok": True,
        "same_place": bool(matches),
        "left_bead_id": left,
        "right_bead_id": right,
        "matches": matches,
        "best_match": matches[0] if matches else None,
        "metadata": dict(derived.get("metadata") or {}),
    }


__all__ = [
    "CALIBRATION_FRACTION",
    "JUNCTION_SCHEMA",
    "derive_junction_projection",
    "resolve_junction_matches",
]
