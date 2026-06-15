"""Dreamer V3 — Assembly Depth (PRD §12).

Assembly Depth estimates a structure's *historical irreducibility*: how much
continuity is required to explain it. It is not age, raw frequency, or
confidence. It is a read-side measurement/projection — it never writes beads,
claims, associations, or overlays, and it is not (yet) a retrieval ranking term.

v1 scope: bead-typed targets (e.g. goals). Each target's factors are computed
over its support set (the target + its 1-hop association neighbourhood), then
percentile-ranked across the population of comparable targets and combined by a
weighted sum minus a normalized anti-factor penalty (PRD §12.4). Weights are
env-tunable; the report always carries the per-factor breakdown.

Non-bead targets (storylines, tensions, identity traits, values) are deliberately
out of scope here and land in later slices.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from core_memory.schema.normalization import normalize_relation_type

ASSEMBLY_DEPTH_SCHEMA = "assembly_depth_report.v1"

# Factor weights — biased toward cross-context recurrence and human confirmation,
# away from single-session/agent-loop signals (PRD §12.4). Env override:
# CORE_MEMORY_ASSEMBLY_DEPTH_W_<FACTOR_UPPER>.
DEFAULT_FACTOR_WEIGHTS: dict[str, float] = {
    "distinct_session_count": 1.5,
    "recurrence_across_sources": 1.2,
    "supporting_bead_count": 1.0,
    "supporting_claim_count": 0.8,
    "human_confirmation_count": 1.5,
    "causal_dependency_count": 1.0,
    "kind_diversity": 0.8,
    "myelinated_path_support": 1.0,
    "retrieval_feedback_support": 0.8,
    "supersession_survival": 1.0,
}

# Anti-factors are already in [0, 1]; weights for the normalized penalty term.
DEFAULT_ANTI_WEIGHTS: dict[str, float] = {
    "single_session_concentration": 1.2,
    "low_confidence_evidence": 1.0,
    "speculative_only_support": 1.2,
    "recently_superseded_evidence": 1.0,
}

_CAUSAL_RELATIONS = {"caused_by", "led_to", "resolves", "supports", "derived_from"}

# Canonical inactive-association statuses (matches graph/traversal, worldlines,
# root_cause). Missing status is treated as active.
_INACTIVE_ASSOC_STATUSES = {"retracted", "superseded", "inactive"}


def _w(name: str, default: float) -> float:
    try:
        return float(os.getenv(f"CORE_MEMORY_ASSEMBLY_DEPTH_W_{name.upper()}", str(default)) or default)
    except Exception:
        return float(default)


def _read_index(root: str | Path) -> dict[str, Any]:
    p = Path(root) / ".beads" / "index.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _percentile_ranks(values: list[float]) -> list[float]:
    """Percentile rank of each value within the population, in [0, 1].

    rank(v) = (#below + 0.5·#equal) / N. A single-element population is
    neutral (0.5) — depth needs a population to be meaningful.
    """
    n = len(values)
    if n <= 1:
        return [0.5] * n
    out: list[float] = []
    for v in values:
        below = sum(1 for x in values if x < v)
        equal = sum(1 for x in values if x == v)
        out.append((below + 0.5 * equal) / float(n))
    return out


def _support_set(target_id: str, beads: dict[str, dict], adjacency: dict[str, set[str]]) -> list[dict]:
    ids = {target_id} | set(adjacency.get(target_id, set()))
    return [beads[i] for i in ids if i in beads]


def _raw_factors(
    target_id: str,
    beads: dict[str, dict],
    adjacency: dict[str, set[str]],
    incident_edges: dict[str, list[tuple[str, str, str]]],
    edge_bonus: dict[str, float],
) -> tuple[dict[str, float], dict[str, float]]:
    """Return (factors, anti_factors) raw values for one target."""
    support = _support_set(target_id, beads, adjacency)
    n = max(1, len(support))

    sessions = {str(b.get("session_id") or "") for b in support}
    sources = {str(b.get("source_system") or "internal") or "internal" for b in support}
    kinds = {str(b.get("type") or "") for b in support}
    claims = sum(len(b.get("claims") or []) for b in support)
    confirmed = sum(
        1 for b in support
        if str(b.get("authority") or "") == "user_confirmed"
        or str(b.get("approval_status") or "") == "approved"
        or str(b.get("confidence_class") or "") == "A"
    )
    superseded = sum(1 for b in support if str(b.get("status") or "").lower() == "superseded")
    c_class = sum(1 for b in support if str(b.get("confidence_class") or "C") == "C")
    speculative = sum(1 for b in support if str(b.get("grounding") or "") == "speculative")
    recall = sum(int(b.get("recall_count") or 0) for b in support)

    edges = incident_edges.get(target_id, [])
    causal = sum(1 for (_s, rel, _d) in edges if rel in _CAUSAL_RELATIONS)
    myel = 0.0
    for (s, rel, d) in edges:
        myel += max(0.0, float(edge_bonus.get(f"{s}|{rel}|{d}", 0.0)))

    factors = {
        "distinct_session_count": float(len(sessions)),
        "recurrence_across_sources": float(len(sources)),
        "supporting_bead_count": float(len(support)),
        "supporting_claim_count": float(claims),
        "human_confirmation_count": float(confirmed),
        "causal_dependency_count": float(causal),
        "kind_diversity": float(len(kinds)),
        "myelinated_path_support": float(myel),
        "retrieval_feedback_support": float(recall),
        "supersession_survival": float(n - superseded),
    }
    anti = {
        "single_session_concentration": 1.0 if len(sessions) <= 1 else 0.0,
        "low_confidence_evidence": c_class / float(n),
        "speculative_only_support": 1.0 if speculative == n else 0.0,
        "recently_superseded_evidence": superseded / float(n),
    }
    return factors, anti


def compute_assembly_depth(
    root: str | Path,
    *,
    target_kind: str = "goal",
    limit: int = 200,
) -> dict[str, Any]:
    """Compute Assembly Depth reports for all targets of ``target_kind``.

    Returns ``{schema, target_kind, reports: [assembly_depth_report.v1...], config}``.
    Deterministic for a fixed store state.
    """
    index = _read_index(root)
    beads = {str(k): v for k, v in (index.get("beads") or {}).items() if isinstance(v, dict)}
    for bid, b in beads.items():
        b.setdefault("id", bid)

    # Build adjacency + incident edges (normalized relations) from associations.
    adjacency: dict[str, set[str]] = {}
    incident_edges: dict[str, list[tuple[str, str, str]]] = {}
    for assoc in (index.get("associations") or []):
        if not isinstance(assoc, dict):
            continue
        # Skip non-current edges so retracted/superseded support can't inflate
        # depth (canonical filter; missing status = active).
        if str(assoc.get("status") or "active").strip().lower() in _INACTIVE_ASSOC_STATUSES:
            continue
        s = str(assoc.get("source_bead") or "").strip()
        d = str(assoc.get("target_bead") or "").strip()
        if not s or not d:
            continue
        rel = normalize_relation_type(assoc.get("relationship"))
        adjacency.setdefault(s, set()).add(d)
        adjacency.setdefault(d, set()).add(s)
        incident_edges.setdefault(s, []).append((s, rel, d))
        incident_edges.setdefault(d, []).append((s, rel, d))

    target_ids = [
        bid for bid, b in beads.items()
        if str(b.get("type") or "").strip().lower() == str(target_kind).strip().lower()
    ][: max(1, int(limit))]

    # Pull myelination edge bonuses (best-effort; empty when disabled).
    edge_bonus: dict[str, float] = {}
    try:
        from core_memory.runtime.observability.myelination import compute_myelination_bonus_map

        edge_bonus = dict((compute_myelination_bonus_map(root).get("bonus_by_edge_key") or {}))
    except Exception:
        edge_bonus = {}

    factor_weights = {k: _w(k, v) for k, v in DEFAULT_FACTOR_WEIGHTS.items()}
    anti_weights = dict(DEFAULT_ANTI_WEIGHTS)

    if not target_ids:
        return {
            "schema": ASSEMBLY_DEPTH_SCHEMA,
            "target_kind": target_kind,
            "reports": [],
            "config": {"factor_weights": factor_weights, "anti_weights": anti_weights, "population": 0},
        }

    raw_factors: dict[str, dict[str, float]] = {}
    raw_anti: dict[str, dict[str, float]] = {}
    for tid in target_ids:
        f, a = _raw_factors(tid, beads, adjacency, incident_edges, edge_bonus)
        raw_factors[tid] = f
        raw_anti[tid] = a

    # Percentile-rank each factor across the population.
    norm_factors: dict[str, dict[str, float]] = {tid: {} for tid in target_ids}
    for fname in DEFAULT_FACTOR_WEIGHTS:
        col = [raw_factors[tid][fname] for tid in target_ids]
        ranks = _percentile_ranks(col)
        for tid, r in zip(target_ids, ranks):
            norm_factors[tid][fname] = round(r, 6)

    w_sum = sum(factor_weights.values()) or 1.0
    p_sum = sum(anti_weights.values()) or 1.0

    reports: list[dict[str, Any]] = []
    for tid in target_ids:
        score = sum(factor_weights[f] * norm_factors[tid][f] for f in DEFAULT_FACTOR_WEIGHTS) / w_sum
        penalty = sum(anti_weights[a] * raw_anti[tid][a] for a in DEFAULT_ANTI_WEIGHTS) / p_sum
        depth = _clamp01(score - penalty)
        interpretation = "high" if depth >= 0.66 else ("medium" if depth >= 0.33 else "low")
        reports.append({
            "schema": ASSEMBLY_DEPTH_SCHEMA,
            "target_kind": target_kind,
            "target_id": tid,
            "score": round(depth, 6),
            "interpretation": interpretation,
            "components": {
                "factors_raw": {k: round(v, 6) for k, v in raw_factors[tid].items()},
                "factors_norm": norm_factors[tid],
                "anti_factors": {k: round(v, 6) for k, v in raw_anti[tid].items()},
                "score_pre_penalty": round(score, 6),
                "penalty": round(penalty, 6),
            },
        })

    reports.sort(key=lambda r: (-float(r["score"]), str(r["target_id"])))
    return {
        "schema": ASSEMBLY_DEPTH_SCHEMA,
        "target_kind": target_kind,
        "reports": reports,
        "config": {"factor_weights": factor_weights, "anti_weights": anti_weights, "population": len(target_ids)},
    }


__all__ = [
    "ASSEMBLY_DEPTH_SCHEMA",
    "DEFAULT_FACTOR_WEIGHTS",
    "DEFAULT_ANTI_WEIGHTS",
    "compute_assembly_depth",
]
