"""Epistemic conflict scoring for subject+slot claim pairs.

Formula (PRD #14):
  time_component = min(1.0, time_delta_days / 180.0)   # 6+ month conflict → 1.0
  seq_component  = 0.0 if gap == 0 else min(1.0, gap / 10.0)  # large unresolved gap → 1.0
  score = 0.6 * time_component + 0.4 * seq_component
"""
from __future__ import annotations

from typing import Any


def compute_epistemic_conflict_score(
    claim_a: dict[str, Any],
    claim_b: dict[str, Any],
    chain_seq_gap: int,
    time_delta_days: float,
) -> float:
    """Score the epistemic pressure of a conflict between two claims.

    Returns a float in [0.0, 1.0]. Higher means older and/or larger seq gap —
    both signals that the conflict was never resolved and deserves human review.
    """
    time_component = min(1.0, max(0.0, float(time_delta_days) / 180.0))
    seq_gap = abs(int(chain_seq_gap))
    seq_component = 0.0 if seq_gap == 0 else min(1.0, seq_gap / 10.0)
    score = 0.6 * time_component + 0.4 * seq_component
    return round(float(min(1.0, max(0.0, score))), 6)


def _parse_created_at(claim: dict[str, Any]) -> float | None:
    """Return a POSIX timestamp from a claim's created_at field, or None."""
    from core_memory.temporal import normalize_as_of
    raw = str(claim.get("created_at") or claim.get("observed_at") or "").strip()
    if not raw:
        return None
    dt = normalize_as_of(raw)
    if dt is None:
        return None
    return dt.timestamp()


def conflict_score_for_pair(claim_a: dict[str, Any], claim_b: dict[str, Any]) -> float:
    """Compute epistemic score directly from two claim dicts.

    Extracts chain_seq and created_at from the dicts; falls back to 0 / 0.0 if missing.
    """
    ts_a = _parse_created_at(claim_a)
    ts_b = _parse_created_at(claim_b)
    if ts_a is not None and ts_b is not None:
        time_delta_days = abs(ts_b - ts_a) / 86400.0
    else:
        time_delta_days = 0.0

    seq_a = int(claim_a.get("chain_seq") or 0) if str(claim_a.get("chain_seq") or "").isdigit() else 0
    seq_b = int(claim_b.get("chain_seq") or 0) if str(claim_b.get("chain_seq") or "").isdigit() else 0
    chain_seq_gap = abs(seq_b - seq_a)

    return compute_epistemic_conflict_score(claim_a, claim_b, chain_seq_gap, time_delta_days)
