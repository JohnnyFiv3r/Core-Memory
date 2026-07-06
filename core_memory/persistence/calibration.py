from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core_memory.persistence.myelination_manifest import read_myelination_manifest
from core_memory.persistence.myelination_rewards import read_reward_events
from core_memory.persistence.retrieval_feedback import (
    _parse_iso,
    read_retrieval_feedback,
)
from core_memory.schema.normalization import normalize_relation_type

CALIBRATION_SCHEMA = "calibration_curve.v1"

_BANDS = [
    ("<0.6", 0.0, 0.6),
    ("0.6-0.7", 0.6, 0.7),
    ("0.7-0.8", 0.7, 0.8),
    ("0.8-0.9", 0.8, 0.9),
    (">=0.9", 0.9, 1.000001),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except Exception:
        return int(default)


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return float(default)


def _edge_key(edge: dict[str, Any]) -> str:
    src = str(edge.get("src") or edge.get("source") or "").strip()
    dst = str(edge.get("dst") or edge.get("target") or "").strip()
    rel = normalize_relation_type(edge.get("rel") or edge.get("relationship") or "")
    if not src or not dst or not rel:
        return ""
    return f"{src}|{rel}|{dst}"


def _judge_prior_by_edge_key(root: str | Path) -> dict[str, float]:
    """Map ``edge_key -> stored association confidence`` (the judge_prior).

    This mirrors the adjacency build in ``retrieval/agent.py`` so the calibration
    X-axis is the same ``effective_confidence = clamp(judge_prior + bonus, 0, 1)``
    the BFS actually traverses on. The myelination manifest only carries
    ``bonus_by_edge_key`` (PRD-A); the per-edge judge_prior lives on the
    association rows in ``index.json``, so it must be read here rather than
    approximated by a single flat default for every edge.
    """
    path = Path(root) / ".beads" / "index.json"
    if not path.exists():
        return {}
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, float] = {}
    for assoc in index.get("associations") or []:
        if not isinstance(assoc, dict):
            continue
        src = str(assoc.get("source_bead") or assoc.get("source_bead_id") or "").strip()
        dst = str(assoc.get("target_bead") or assoc.get("target_bead_id") or "").strip()
        rel = normalize_relation_type(assoc.get("relationship") or "")
        if not src or not dst or not rel:
            continue
        raw_conf = assoc.get("confidence")
        try:
            judge_prior = max(0.0, min(1.0, float(raw_conf))) if raw_conf is not None else 0.85
        except (TypeError, ValueError):
            judge_prior = 0.85
        out[f"{src}|{rel}|{dst}"] = judge_prior
    return out


def _confidence(
    edge_key: str,
    manifest: dict[str, Any],
    judge_prior_by_edge_key: dict[str, float],
    limitations: set[str],
) -> float:
    explicit = dict(manifest.get("effective_confidence_by_edge_key") or {})
    if edge_key in explicit:
        try:
            return max(0.0, min(1.0, float(explicit[edge_key])))
        except Exception:
            pass
    bonus = float(dict(manifest.get("bonus_by_edge_key") or {}).get(edge_key) or 0.0)
    if edge_key in judge_prior_by_edge_key:
        judge_prior = judge_prior_by_edge_key[edge_key]
    else:
        judge_prior = _float_env("CORE_MEMORY_CALIBRATION_DEFAULT_JUDGE_PRIOR", 0.85)
        limitations.add("judge_prior_unavailable_for_some_edges")
    return max(0.0, min(1.0, judge_prior + bonus))


def _band_slot(confidence: float) -> int:
    for idx, (_label, lo, hi) in enumerate(_BANDS):
        if confidence >= lo and confidence < hi:
            return idx
    return len(_BANDS) - 1


def _negative_corrections(root: str | Path, since: str, correction_window_hours: int) -> list[dict[str, Any]]:
    rows = read_reward_events(root, since=since, limit=_int_env("CORE_MEMORY_CALIBRATION_REWARD_LIMIT", 5000))
    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("polarity") or "").lower() != "negative":
            continue
        if str(row.get("source_type") or "").lower() not in {"human_rejection", "claim_conflict_resolution", "overlay_decision"}:
            continue
        edge_keys = {str(k) for k in (row.get("edge_keys") or []) if str(k)}
        if not edge_keys:
            continue
        out.append(
            {
                "created_at": _parse_iso(str(row.get("created_at") or "")),
                "edge_keys": edge_keys,
                "window": timedelta(hours=max(1, int(correction_window_hours))),
            }
        )
    return out


def _has_subsequent_correction(row_dt: datetime | None, edge_key: str, corrections: list[dict[str, Any]]) -> bool:
    for corr in corrections:
        if edge_key not in corr["edge_keys"]:
            continue
        corr_dt = corr.get("created_at")
        if row_dt is None or corr_dt is None:
            return True
        if corr_dt >= row_dt and corr_dt <= row_dt + corr["window"]:
            return True
    return False


def _rank(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        rank = (i + j + 2) / 2.0
        for idx in range(i, j + 1):
            ranks[ordered[idx][0]] = rank
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(ys) < 2:
        return None
    rx = _rank(xs)
    ry = _rank(ys)
    mx = sum(rx) / len(rx)
    my = sum(ry) / len(ry)
    num = sum((x - mx) * (y - my) for x, y in zip(rx, ry))
    denx = math.sqrt(sum((x - mx) ** 2 for x in rx))
    deny = math.sqrt(sum((y - my) ** 2 for y in ry))
    if denx <= 1e-12 or deny <= 1e-12:
        return None
    return round(float(num / (denx * deny)), 6)


def compute_calibration_curve(
    root: str | Path,
    *,
    since: str | None = None,
    correction_window_hours: int | None = None,
) -> dict[str, Any]:
    window = str(since or os.getenv("CORE_MEMORY_MYELINATION_SINCE", "30d"))
    correction_hours = int(correction_window_hours or _int_env("CORE_MEMORY_CALIBRATION_CORRECTION_WINDOW_HOURS", 72))
    min_events = _int_env("CORE_MEMORY_CALIBRATION_MIN_EVENTS", 20)
    min_band_count = _int_env("CORE_MEMORY_CALIBRATION_MIN_BAND_EVENTS", 1)
    high_band_min = _float_env("CORE_MEMORY_CALIBRATION_HIGH_BAND_MIN_USEFULNESS", 0.80)
    rho_min = _float_env("CORE_MEMORY_CALIBRATION_MIN_SPEARMAN_RHO", 0.70)

    rows = read_retrieval_feedback(root, since=window, limit=_int_env("CORE_MEMORY_CALIBRATION_FEEDBACK_LIMIT", 5000))
    manifest = read_myelination_manifest(root)
    judge_prior_by_edge_key = _judge_prior_by_edge_key(root)
    limitations: set[str] = set()
    if not manifest.get("present"):
        limitations.add("myelination_manifest_unavailable")
    corrections = _negative_corrections(root, window, correction_hours)

    band_rows = [
        {
            "label": label,
            "lower": lo,
            "upper": None if label.startswith(">=") else hi,
            "min": lo,
            "max": None if label.startswith(">=") else hi,
            "recall_count": 0,
            "event_count": 0,
            "correction_free_count": 0,
            "positive_count": 0,
            "negative_correction_count": 0,
            "realized_usefulness_rate": None,
            "usefulness_rate": None,
            "confidence_sum": 0.0,
            "avg_effective_confidence": None,
        }
        for label, lo, hi in _BANDS
    ]
    predictions: list[float] = []
    outcomes: list[float] = []

    for row in rows:
        response = dict(row.get("response") or {})
        edges = {_edge_key(edge) for edge in (response.get("edges") or []) if isinstance(edge, dict)}
        edges.discard("")
        if not edges:
            limitations.add("feedback_edges_unavailable")
            continue
        row_dt = _parse_iso(str(row.get("created_at") or ""))
        for edge_key in sorted(edges):
            conf = _confidence(edge_key, manifest, judge_prior_by_edge_key, limitations)
            slot = band_rows[_band_slot(conf)]
            slot["recall_count"] += 1
            slot["event_count"] += 1
            slot["confidence_sum"] = float(slot["confidence_sum"]) + conf
            corrected = _has_subsequent_correction(row_dt, edge_key, corrections)
            if corrected:
                slot["negative_correction_count"] += 1
            useful = bool(row.get("success")) and not corrected
            predictions.append(float(conf))
            outcomes.append(1.0 if useful else 0.0)
            if useful:
                slot["correction_free_count"] += 1
                slot["positive_count"] += 1

    included_x: list[float] = []
    included_y: list[float] = []
    ece = 0.0
    sample_count = len(predictions)
    for band in band_rows:
        count = int(band["recall_count"])
        if count:
            rate = int(band["correction_free_count"]) / float(count)
            band["realized_usefulness_rate"] = round(rate, 6)
            band["usefulness_rate"] = round(rate, 6)
            band["avg_effective_confidence"] = round(float(band["confidence_sum"]) / float(count), 6)
            if sample_count:
                ece += (count / float(sample_count)) * abs(float(band["avg_effective_confidence"]) - rate)
        band.pop("confidence_sum", None)
        if count >= min_band_count and band["realized_usefulness_rate"] is not None:
            upper = 1.0 if band["upper"] is None else float(band["upper"])
            included_x.append((float(band["lower"]) + upper) / 2.0)
            included_y.append(float(band["realized_usefulness_rate"]))

    rho = _spearman(included_x, included_y)
    brier = None
    if predictions:
        brier = sum((p - y) ** 2 for p, y in zip(predictions, outcomes)) / float(len(predictions))
    high = band_rows[-1]["realized_usefulness_rate"]
    if len(rows) < min_events or len(included_x) < 2:
        status = "insufficient_data"
    elif rho is not None and rho >= rho_min and high is not None and float(high) >= high_band_min:
        status = "good"
    else:
        status = "degraded"

    return {
        "schema": CALIBRATION_SCHEMA,
        "window": window,
        "generated_at": _now(),
        "status": status,
        "spearman_rho": rho,
        "expected_calibration_error": round(float(ece), 6) if sample_count else None,
        "brier_score": round(float(brier), 6) if brier is not None else None,
        "high_band_usefulness_rate": high,
        "band_count": len(_BANDS),
        "bands_included": len(included_x),
        "sample_count": int(sample_count),
        "bands": band_rows,
        "event_count": len(rows),
        "correction_count": len(corrections),
        "correction_event_count": len(corrections),
        "auto_mode_gate": "open" if status == "good" else "paused",
        "limitations": sorted(limitations),
    }


__all__ = ["CALIBRATION_SCHEMA", "compute_calibration_curve"]
