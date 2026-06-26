from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from core_memory.runtime.observability.calibration import compute_calibration_curve
from core_memory.runtime.observability.retrieval_feedback import record_retrieval_feedback
from core_memory.schema.normalization import normalize_relation_type

from benchmarks.causal.runner import _env_overrides, _repo_commit
from benchmarks.contracts import BenchmarkShortcutFlags

T2_FIXTURE_SCHEMA = "causal_continuity.t2_fixture.v1"
T2_REPORT_SCHEMA = "causal_continuity.t2_calibration.v1"


def default_fixture_path() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "t2_calibration_seed.json"


def _load_fixture(path: Path | None = None) -> dict[str, Any]:
    p = path or default_fixture_path()
    payload = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"t2_fixture_not_object:{p}")
    if str(payload.get("schema") or "") != T2_FIXTURE_SCHEMA:
        raise ValueError(f"t2_fixture_schema_mismatch:{p}")
    edges = payload.get("edges")
    if not isinstance(edges, list) or not edges:
        raise ValueError(f"t2_fixture_edges_invalid:{p}")
    return payload


def _edge_key(edge: dict[str, Any]) -> str:
    src = str(edge.get("source") or "").strip()
    dst = str(edge.get("target") or "").strip()
    rel = normalize_relation_type(edge.get("relationship") or "")
    if not src or not dst or not rel:
        raise ValueError("t2_fixture_edge_incomplete")
    return f"{src}|{rel}|{dst}"


def _write_index_associations(root: str | Path, edges: list[dict[str, Any]]) -> None:
    associations: list[dict[str, Any]] = []
    for edge in edges:
        associations.append({
            "source_bead": str(edge.get("source") or "").strip(),
            "target_bead": str(edge.get("target") or "").strip(),
            "relationship": normalize_relation_type(edge.get("relationship") or ""),
            "confidence": float(edge.get("judge_prior") if edge.get("judge_prior") is not None else 0.85),
            "provenance": "benchmark_fixture",
        })

    p = Path(root) / ".beads" / "index.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"beads": {}, "associations": associations}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_manifest(root: str | Path, edges: list[dict[str, Any]], *, manifest_bonus_enabled: bool = True) -> None:
    bonus_by_edge_key = {
        _edge_key(edge): (float(edge.get("manifest_bonus") or 0.0) if manifest_bonus_enabled else 0.0)
        for edge in edges
    }
    p = Path(root) / ".beads" / "events" / "myelination-manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "schema": "core_memory.myelination_manifest.v2",
                "enabled": True,
                "bonus_by_edge_key": bonus_by_edge_key,
                "bonus_by_bead_id": {},
                "stats": {
                    "events": 0,
                    "edges": len(bonus_by_edge_key),
                    "beads": 0,
                    "strengthened": sum(1 for v in bonus_by_edge_key.values() if v > 0),
                    "weakened": sum(1 for v in bonus_by_edge_key.values() if v < 0),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _record_outcomes(root: str | Path, fixture: dict[str, Any]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for edge in fixture.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        edge_key = _edge_key(edge)
        outcomes = list(edge.get("outcomes") or [])
        src, rel, dst = edge_key.split("|", 2)
        effective_confidence = float(edge.get("judge_prior") or 0.0) + float(edge.get("manifest_bonus") or 0.0)
        for idx, outcome in enumerate(outcomes, start=1):
            useful = bool(outcome)
            record_retrieval_feedback(
                root,
                request={
                    "query": f"calibration probe {fixture.get('id')} {edge_key} {idx}",
                    "intent": "causal_calibration",
                    "k": 5,
                },
                response={
                    "ok": useful,
                    "answer_outcome": "answer_current" if useful else "abstain",
                    "results": [{"bead_id": dst, "score": effective_confidence, "source_surface": "calibration_fixture"}],
                    "chains": [{"edges": [{"src": src, "rel": rel, "dst": dst}]}],
                },
                source="causal_continuity_t2",
                usefulness="helpful" if useful else "not_helpful",
            )
            samples.append({
                "edge_key": edge_key,
                "effective_confidence": round(max(0.0, min(1.0, effective_confidence)), 6),
                "useful": useful,
            })
    return samples


def _seed_fixture(
    root: str | Path,
    fixture: dict[str, Any],
    *,
    manifest_bonus_enabled: bool = True,
    record_validated_outcomes: bool = True,
) -> list[dict[str, Any]]:
    edges = [dict(e) for e in (fixture.get("edges") or []) if isinstance(e, dict)]
    _write_index_associations(root, edges)
    _write_manifest(root, edges, manifest_bonus_enabled=manifest_bonus_enabled)
    if not record_validated_outcomes:
        return []
    return _record_outcomes(root, fixture)


def _checks(curve: dict[str, Any], targets: dict[str, Any]) -> dict[str, bool]:
    rho = curve.get("spearman_rho")
    high = curve.get("high_band_usefulness_rate")
    ece = curve.get("expected_calibration_error")
    brier = curve.get("brier_score")
    return {
        "sufficient_data": int(curve.get("sample_count") or 0) >= int(targets.get("min_sample_count") or 1),
        "rho": rho is not None and float(rho) >= float(targets.get("min_spearman_rho") or 0.70),
        "high_band_usefulness": high is not None and float(high) >= float(targets.get("min_high_band_usefulness") or 0.80),
        "expected_calibration_error": ece is not None and float(ece) <= float(targets.get("max_expected_calibration_error") or 1.0),
        "brier_score": brier is not None and float(brier) <= float(targets.get("max_brier_score") or 1.0),
    }


def run_t2_calibration(
    *,
    fixture_path: Path | None = None,
    since: str = "",
    manifest_bonus_enabled: bool = True,
    record_validated_outcomes: bool = True,
) -> dict[str, Any]:
    fixture = _load_fixture(fixture_path)
    targets = dict(fixture.get("targets") or {})
    t0 = time.perf_counter()
    td = tempfile.mkdtemp(prefix="cm-t2-calibration-")
    try:
        env = {
            "CORE_MEMORY_CALIBRATION_MIN_EVENTS": str(targets.get("min_sample_count") or 20),
            "CORE_MEMORY_CALIBRATION_MIN_BAND_EVENTS": "1",
            "CORE_MEMORY_CALIBRATION_HIGH_BAND_MIN_USEFULNESS": str(targets.get("min_high_band_usefulness") or 0.80),
            "CORE_MEMORY_CALIBRATION_MIN_SPEARMAN_RHO": str(targets.get("min_spearman_rho") or 0.70),
        }
        with _env_overrides(env):
            samples = _seed_fixture(
                td,
                fixture,
                manifest_bonus_enabled=manifest_bonus_enabled,
                record_validated_outcomes=record_validated_outcomes,
            )
            curve = compute_calibration_curve(td, since=since)
    finally:
        shutil.rmtree(td, ignore_errors=True)

    checks = _checks(curve, targets)
    flags = BenchmarkShortcutFlags().to_dict()
    return {
        "schema_version": T2_REPORT_SCHEMA,
        "task_id": "t2_calibration_reliability",
        "capability": "C2_confidence_calibration",
        "case_id": str(fixture.get("id") or "calibration_fixture"),
        "description": str(fixture.get("description") or ""),
        "generated_from": str(fixture_path or default_fixture_path()),
        "metadata": {
            "runner": "causal_continuity.t2",
            "commit": _repo_commit(),
            "faithfulness": flags,
            "shortcut_flags": flags,
            "notes": [
                "effective_confidence_equals_judge_prior_plus_manifest_bonus",
                "validated_outcome_feedback_seed",
                "no_retrieval_time_gold_leakage",
            ],
            "ablation_mode": {
                "manifest_bonus_enabled": bool(manifest_bonus_enabled),
                "record_validated_outcomes": bool(record_validated_outcomes),
            },
        },
        "targets": targets,
        "metrics": {
            "spearman_rho": curve.get("spearman_rho"),
            "expected_calibration_error": curve.get("expected_calibration_error"),
            "brier_score": curve.get("brier_score"),
            "high_band_usefulness_rate": curve.get("high_band_usefulness_rate"),
            "sample_count": int(curve.get("sample_count") or 0),
            "event_count": int(curve.get("event_count") or 0),
            "bands_included": int(curve.get("bands_included") or 0),
            "auto_mode_gate": str(curve.get("auto_mode_gate") or ""),
            "status": str(curve.get("status") or ""),
        },
        "checks": checks,
        "pass": all(checks.values()),
        "curve": curve,
        "sample_preview": samples[:10],
        "latency_ms": round((time.perf_counter() - t0) * 1000.0, 3),
    }


__all__ = [
    "T2_FIXTURE_SCHEMA",
    "T2_REPORT_SCHEMA",
    "default_fixture_path",
    "run_t2_calibration",
]
