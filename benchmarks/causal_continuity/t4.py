from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.dreamer.candidates import (
    decide_dreamer_candidate,
    enqueue_dreamer_candidates,
    list_dreamer_candidates,
)
from core_memory.runtime.dreamer.longitudinal import longitudinal_benchmark_v2
from core_memory.runtime.observability.self_model_drift import compute_self_model_drift
from core_memory.soul.goals import approve_goal, list_goals, propose_goal
from core_memory.soul.store import propose_soul_update

from benchmarks.causal.runner import _env_overrides, _repo_commit
from benchmarks.contracts import BenchmarkShortcutFlags

T4_FIXTURE_SCHEMA = "causal_continuity.t4_fixture.v1"
T4_REPORT_SCHEMA = "causal_continuity.t4_longitudinal_continuity.v1"

_PERSISTENT_GOAL_STATES = {"endorsed", "active", "decaying", "completed"}


def default_fixture_path() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "t4_longitudinal_continuity.json"


def _load_fixture(path: Path | None = None) -> dict[str, Any]:
    p = path or default_fixture_path()
    payload = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"t4_fixture_not_object:{p}")
    if str(payload.get("schema") or "") != T4_FIXTURE_SCHEMA:
        raise ValueError(f"t4_fixture_schema_mismatch:{p}")
    beads = payload.get("beads")
    if not isinstance(beads, list) or not beads:
        raise ValueError(f"t4_fixture_beads_invalid:{p}")
    return payload


def _materialize_beads(root: str | Path, fixture: dict[str, Any]) -> dict[str, str]:
    store = MemoryStore(str(root))
    bead_keys: dict[str, str] = {}
    for row in list(fixture.get("beads") or []):
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        bead_id = store.add_bead(
            type=str(row.get("type") or "context"),
            title=str(row.get("title") or "longitudinal fixture bead"),
            summary=list(row.get("summary") or ["longitudinal fixture"]),
            detail=str(row.get("detail") or ""),
            session_id=str(row.get("session_id") or "t4"),
            source_turn_ids=list(row.get("source_turn_ids") or [f"fx-{key or 'bead'}"]),
            incident_keys=list(row.get("incident_keys") or []),
            topics=list(row.get("topics") or []),
            tags=list(row.get("tags") or ["benchmark_longitudinal_continuity"]),
        )
        if key:
            bead_keys[key] = bead_id
    return bead_keys


def _association_rows(fixture: dict[str, Any], bead_keys: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in list(fixture.get("dreamer_candidates") or []):
        if not isinstance(raw, dict):
            continue
        src = bead_keys.get(str(raw.get("source_key") or "").strip())
        tgt = bead_keys.get(str(raw.get("target_key") or "").strip())
        if not src or not tgt:
            continue
        row = dict(raw)
        row["source"] = src
        row["target"] = tgt
        rows.append(row)
    return rows


def _find_candidate(pending: list[dict[str, Any]], desired: dict[str, Any]) -> dict[str, Any]:
    src = str(desired.get("source") or "")
    tgt = str(desired.get("target") or "")
    signal = str(desired.get("relationship") or "").strip().lower()
    for candidate in pending:
        if (
            str(candidate.get("source_bead_id") or "") == src
            and str(candidate.get("target_bead_id") or "") == tgt
            and str(candidate.get("relationship_signal") or candidate.get("relationship") or "").strip().lower() == signal
        ):
            return candidate
    raise ValueError(f"t4_candidate_not_found:{signal}:{src}:{tgt}")


def _applied_summary(applied: Any) -> dict[str, Any]:
    row = dict(applied or {}) if isinstance(applied, dict) else {}
    keys = [
        "ok",
        "association_id",
        "canonical_entry",
        "turn_id",
        "session_id",
        "relationship",
        "relationship_raw",
        "appended_count",
        "application_mode",
        "error",
    ]
    return {k: row.get(k) for k in keys if k in row}


def _decide_candidates(root: str | Path, associations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enqueue_dreamer_candidates(
        root=root,
        associations=associations,
        run_metadata={"run_id": "t4-longitudinal-continuity", "mode": "suggest", "session_id": "t4-session-a"},
    )
    pending = list_dreamer_candidates(root=root, status="pending", limit=100).get("results") or []
    decisions: list[dict[str, Any]] = []
    for row in associations:
        candidate = _find_candidate([dict(c) for c in pending if isinstance(c, dict)], row)
        decision = decide_dreamer_candidate(
            root=root,
            candidate_id=str(candidate.get("id") or ""),
            decision=str(row.get("decision") or "accept"),
            reviewer="causal_continuity_t4",
            apply=bool(row.get("apply")),
        )
        decisions.append({
            "candidate_id": str(candidate.get("id") or ""),
            "relationship": str(candidate.get("relationship") or ""),
            "relationship_signal": str(candidate.get("relationship_signal") or ""),
            "hypothesis_type": str(candidate.get("hypothesis_type") or ""),
            "apply": bool(row.get("apply")),
            "status": str(decision.get("status") or ""),
            "ok": bool(decision.get("ok")),
            "applied": _applied_summary(decision.get("applied")),
        })
    return decisions


def _record_downstream_use(root: str | Path, fixture: dict[str, Any], bead_keys: dict[str, str]) -> list[str]:
    store = MemoryStore(str(root))
    recalled: list[str] = []
    for key in list(fixture.get("downstream_recall_bead_keys") or []):
        bead_id = bead_keys.get(str(key))
        if not bead_id:
            continue
        store.recall(bead_id)
        recalled.append(bead_id)
    return recalled


def _write_soul_updates(root: str | Path, fixture: dict[str, Any], bead_keys: dict[str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in list(fixture.get("soul_updates") or []):
        if not isinstance(row, dict):
            continue
        evidence = [
            {"type": "bead", "id": bead_keys[key]}
            for key in [str(k) for k in list(row.get("evidence_bead_keys") or [])]
            if key in bead_keys
        ]
        out.append(
            propose_soul_update(
                root,
                target_file=str(row.get("target_file") or "IDENTITY.md"),
                entry_key=str(row.get("entry_key") or "Continuity"),
                content=str(row.get("content") or ""),
                epistemic_status=str(row.get("epistemic_status") or "endorsed"),
                evidence=evidence,
                requires_approval=False,
                source="agent",
                reason="causal continuity T4 grounded self-model stability probe",
            )
        )
    return out


def _write_goals(root: str | Path, fixture: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in list(fixture.get("goals") or []):
        if not isinstance(row, dict):
            continue
        proposed = propose_goal(
            str(root),
            title=str(row.get("title") or "Maintain continuity"),
            statement=str(row.get("statement") or ""),
            goal_id=str(row.get("goal_id") or "") or None,
            success_criteria=list(row.get("success_criteria") or []),
            subject="self",
            actor="causal_continuity_t4",
            reason="causal continuity T4 goal-thread persistence probe",
        )
        row_out = {"proposed": proposed}
        if bool(row.get("approve")) and bool(proposed.get("ok")):
            row_out["approved"] = approve_goal(
                str(root),
                goal_id=str(proposed.get("goal_id") or ""),
                subject="self",
                actor="causal_continuity_t4",
                reason="approved for T4 continuity persistence probe",
            )
        out.append(row_out)
    return out


def _goal_persistence(root: str | Path) -> dict[str, Any]:
    goals = list(list_goals(str(root), subject="self", include_terminal=True).get("goals") or [])
    total = len(goals)
    persisted = [g for g in goals if str((g or {}).get("state") or "").lower() in _PERSISTENT_GOAL_STATES]
    return {
        "goal_count": int(total),
        "persistent_goal_count": int(len(persisted)),
        "persistent_states": sorted(_PERSISTENT_GOAL_STATES),
        "goal_thread_persistence_rate": round((len(persisted) / float(total)), 4) if total else 0.0,
        "goals": goals,
    }


def _checks(metrics: dict[str, Any], targets: dict[str, Any]) -> dict[str, bool]:
    return {
        "continuity_lift": float(metrics.get("continuity_lift") or 0.0) >= float(targets.get("min_continuity_lift") or 0.0),
        "self_model_drift": int(metrics.get("self_model_drift_score") or 0) <= int(targets.get("max_self_model_drift_score") or 0)
        and str(metrics.get("self_model_drift_status") or "") == "healthy",
        "goal_thread_persistence": float(metrics.get("goal_thread_persistence_rate") or 0.0) >= float(targets.get("min_goal_thread_persistence_rate") or 1.0),
    }


def run_t4_longitudinal_continuity(*, fixture_path: Path | None = None) -> dict[str, Any]:
    fixture = _load_fixture(fixture_path)
    targets = dict(fixture.get("targets") or {})
    since = str(fixture.get("since") or "30d")
    t0 = time.perf_counter()
    td = tempfile.mkdtemp(prefix="cm-t4-longitudinal-")
    try:
        with _env_overrides({
            "CORE_MEMORY_SEMANTIC_AUTODRAIN": "off",
            "CORE_MEMORY_VECTOR_BACKEND": "local-faiss",
        }):
            bead_keys = _materialize_beads(td, fixture)
            associations = _association_rows(fixture, bead_keys)
            decisions = _decide_candidates(td, associations)
            recalled_bead_ids = _record_downstream_use(td, fixture, bead_keys)
            soul_results = _write_soul_updates(td, fixture, bead_keys)
            goal_results = _write_goals(td, fixture)

            longitudinal = longitudinal_benchmark_v2(td, since=since)
            self_model_drift = compute_self_model_drift(td, since=since)
            goal_threads = _goal_persistence(td)
    finally:
        shutil.rmtree(td, ignore_errors=True)

    comparisons = dict(longitudinal.get("comparisons") or {})
    cohorts = dict(longitudinal.get("cohorts") or {})
    with_dreamer = dict(cohorts.get("core_memory_with_dreamer") or {})
    with_dreamer_counts = dict(with_dreamer.get("counts") or {})
    with_dreamer_rates = dict(with_dreamer.get("rates") or {})
    metrics = {
        "continuity_lift": float(comparisons.get("core_with_dreamer_vs_no_memory_lift") or 0.0),
        "self_model_drift_score": int(self_model_drift.get("drift_score") or 0),
        "self_model_drift_status": str(self_model_drift.get("status") or ""),
        "goal_thread_persistence_rate": float(goal_threads.get("goal_thread_persistence_rate") or 0.0),
        "accepted_applied_structural_candidates": int(with_dreamer_counts.get("accepted_applied") or 0),
        "cross_session_transfer_success_rate": float(with_dreamer_rates.get("cross_session_transfer_success_rate") or 0.0),
        "downstream_use_rate": float(with_dreamer_rates.get("downstream_use_rate") or 0.0),
        "quality_score": float(with_dreamer_rates.get("quality_score") or 0.0),
    }
    checks = _checks(metrics, targets)
    flags = BenchmarkShortcutFlags().to_dict()
    return {
        "schema_version": T4_REPORT_SCHEMA,
        "task_id": "t4_longitudinal_continuity",
        "capability": "C4_continuity_self_model_stability",
        "case_id": str(fixture.get("id") or "longitudinal_continuity_fixture"),
        "description": str(fixture.get("description") or ""),
        "generated_from": str(fixture_path or default_fixture_path()),
        "metadata": {
            "runner": "causal_continuity.t4",
            "commit": _repo_commit(),
            "faithfulness": flags,
            "shortcut_flags": flags,
            "notes": [
                "longitudinal_benchmark_v2_memory_vs_no_memory_lift",
                "self_model_drift_meter_grounded_identity_stability",
                "goal_thread_persistence_from_goal_beads",
            ],
        },
        "targets": targets,
        "metrics": metrics,
        "checks": checks,
        "pass": all(checks.values()),
        "setup": {
            "bead_count": len(bead_keys),
            "bead_keys": bead_keys,
            "dreamer_decisions": decisions,
            "downstream_recalled_bead_ids": recalled_bead_ids,
            "soul_results": soul_results,
            "goal_results": goal_results,
        },
        "longitudinal": longitudinal,
        "self_model_drift": self_model_drift,
        "goal_threads": goal_threads,
        "latency_ms": round((time.perf_counter() - t0) * 1000.0, 3),
    }


__all__ = [
    "T4_FIXTURE_SCHEMA",
    "T4_REPORT_SCHEMA",
    "default_fixture_path",
    "run_t4_longitudinal_continuity",
]
