from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from core_memory.claim.resolver import resolve_all_current_state
from core_memory.persistence.store import MemoryStore
from core_memory.persistence.store_claim_ops import write_claim_updates_to_bead, write_claims_to_bead

from benchmarks.causal.runner import _env_overrides, _repo_commit
from benchmarks.contracts import BenchmarkShortcutFlags

T3_FIXTURE_SCHEMA = "causal_continuity.t3_fixture.v1"
T3_REPORT_SCHEMA = "causal_continuity.t3_temporal_state.v1"


def default_fixture_path() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "t3_temporal_state.json"


def _load_fixture(path: Path | None = None) -> dict[str, Any]:
    p = path or default_fixture_path()
    payload = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"t3_fixture_not_object:{p}")
    if str(payload.get("schema") or "") != T3_FIXTURE_SCHEMA:
        raise ValueError(f"t3_fixture_schema_mismatch:{p}")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"t3_fixture_cases_invalid:{p}")
    return payload


def _materialize_case(root: str | Path, case: dict[str, Any]) -> dict[str, str]:
    store = MemoryStore(str(root))
    setup = dict(case.get("setup") or {})
    bead_keys: dict[str, str] = {}

    for row in list(setup.get("beads") or []):
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        bead_id = store.add_bead(
            type=str(row.get("type") or "context"),
            title=str(row.get("title") or "temporal fixture bead"),
            summary=list(row.get("summary") or ["temporal fixture"]),
            detail=str(row.get("detail") or ""),
            session_id=str(row.get("session_id") or "t3"),
            source_turn_ids=list(row.get("source_turn_ids") or [f"fx-{key or 'bead'}"]),
            tags=list(row.get("tags") or ["benchmark_temporal_state"]),
        )
        if key:
            bead_keys[key] = bead_id

    fallback_bead_id = next(iter(bead_keys.values()), "")
    for group in list(setup.get("claims") or []):
        if not isinstance(group, dict):
            continue
        bead_id = bead_keys.get(str(group.get("bead_key") or "").strip()) or fallback_bead_id
        if bead_id:
            write_claims_to_bead(str(root), bead_id, list(group.get("rows") or []))

    for group in list(setup.get("claim_updates") or []):
        if not isinstance(group, dict):
            continue
        bead_id = bead_keys.get(str(group.get("bead_key") or "").strip()) or fallback_bead_id
        if bead_id:
            write_claim_updates_to_bead(str(root), bead_id, list(group.get("rows") or []))

    return bead_keys


def _current_value(slot_row: dict[str, Any]) -> Any:
    current = slot_row.get("current_claim") if isinstance(slot_row.get("current_claim"), dict) else {}
    return current.get("value")


def _current_claim_id(slot_row: dict[str, Any]) -> str:
    current = slot_row.get("current_claim") if isinstance(slot_row.get("current_claim"), dict) else {}
    return str(current.get("id") or "")


def _ids(rows: list[Any]) -> set[str]:
    out: set[str] = set()
    for row in rows or []:
        if isinstance(row, dict):
            text = str(row.get("id") or "").strip()
        else:
            text = str(row or "").strip()
        if text:
            out.add(text)
    return out


def _evaluate_case(case: dict[str, Any], *, state: dict[str, Any]) -> dict[str, Any]:
    expected = dict(case.get("expected") or {})
    slot_key = str(expected.get("slot") or "").strip()
    slot_row = dict((state.get("slots") or {}).get(slot_key) or {})
    status = str(slot_row.get("status") or "not_found")
    current_value = _current_value(slot_row)
    current_claim_id = _current_claim_id(slot_row)
    conflicts = [dict(x) for x in list(slot_row.get("conflicts") or []) if isinstance(x, dict)]

    checks: dict[str, bool] = {}
    expected_status = str(expected.get("status") or "").strip()
    if expected_status:
        checks["status"] = status == expected_status

    if "value" in expected:
        checks["value"] = current_value == expected.get("value")

    excluded_claim_ids = {str(x) for x in list(expected.get("excluded_current_claim_ids") or []) if str(x).strip()}
    if excluded_claim_ids:
        checks["superseded_claim_not_current"] = current_claim_id not in excluded_claim_ids

    excluded_values = {str(x) for x in list(expected.get("excluded_current_values") or []) if str(x).strip()}
    if excluded_values:
        checks["excluded_value_not_current"] = str(current_value) not in excluded_values

    conflict_claim_ids = {str(x) for x in list(expected.get("conflict_claim_ids") or []) if str(x).strip()}
    if conflict_claim_ids or expected_status == "conflict":
        conflict_ids = _ids(conflicts)
        checks["conflict_surfaced"] = status == "conflict" and bool(conflicts)
        if conflict_claim_ids:
            checks["expected_conflict_claims"] = conflict_claim_ids.issubset(conflict_ids)

    return {
        "case_id": str(case.get("id") or ""),
        "bucket_labels": list(case.get("bucket_labels") or []),
        "query": str(case.get("query") or ""),
        "as_of": str(case.get("as_of") or "") or None,
        "slot": slot_key,
        "expected": expected,
        "actual": {
            "status": status,
            "current_value": current_value,
            "current_claim_id": current_claim_id,
            "conflict_claim_ids": sorted(_ids(conflicts)),
            "timeline_event_count": int(len(list(slot_row.get("timeline") or []))),
        },
        "checks": checks,
        "pass": all(checks.values()) if checks else False,
    }


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    scoped = [r for r in rows if key in dict(r.get("checks") or {})]
    if not scoped:
        return None
    return round(
        sum(1 for r in scoped if bool((r.get("checks") or {}).get(key))) / float(len(scoped)),
        4,
    )


def _any_check_rate(rows: list[dict[str, Any]], keys: set[str]) -> tuple[int, float | None]:
    scoped = [r for r in rows if keys.intersection(set((r.get("checks") or {}).keys()))]
    if not scoped:
        return 0, None

    def _row_ok(row: dict[str, Any]) -> bool:
        checks = dict(row.get("checks") or {})
        values = [bool(v) for k, v in checks.items() if k in keys]
        return all(values) if values else False

    return len(scoped), round(sum(1 for r in scoped if _row_ok(r)) / float(len(scoped)), 4)


def run_t3_temporal_state(*, fixture_path: Path | None = None) -> dict[str, Any]:
    fixture = _load_fixture(fixture_path)
    targets = dict(fixture.get("targets") or {})
    t0 = time.perf_counter()
    case_rows: list[dict[str, Any]] = []

    for case in list(fixture.get("cases") or []):
        if not isinstance(case, dict):
            continue
        td = tempfile.mkdtemp(prefix="cm-t3-temporal-")
        try:
            t_setup = time.perf_counter()
            with _env_overrides({
                "CORE_MEMORY_SEMANTIC_AUTODRAIN": "off",
                "CORE_MEMORY_VECTOR_BACKEND": "local-faiss",
            }):
                _materialize_case(td, case)
            setup_ms = (time.perf_counter() - t_setup) * 1000.0

            t_resolve = time.perf_counter()
            state = resolve_all_current_state(td, as_of=str(case.get("as_of") or "") or None)
            resolve_ms = (time.perf_counter() - t_resolve) * 1000.0

            row = _evaluate_case(case, state=state)
            row["write_setup_ms"] = round(setup_ms, 3)
            row["resolve_ms"] = round(resolve_ms, 3)
            case_rows.append(row)
        finally:
            shutil.rmtree(td, ignore_errors=True)

    total = len(case_rows)
    passed = sum(1 for r in case_rows if bool(r.get("pass")))
    as_of_rows = [r for r in case_rows if str(r.get("as_of") or "").strip()]
    as_of_cases, as_of_accuracy = _any_check_rate(as_of_rows, {"value"})
    supersession_cases, supersession_rate = _any_check_rate(
        case_rows,
        {"superseded_claim_not_current", "excluded_value_not_current"},
    )
    contradiction_cases, contradiction_rate = _any_check_rate(
        case_rows,
        {"conflict_surfaced", "expected_conflict_claims"},
    )
    correct_state_rate = round((passed / float(total)), 4) if total else 0.0

    checks = {
        "correct_state_selection": correct_state_rate >= float(targets.get("min_correct_state_selection_rate") or 1.0),
        "as_of_accuracy": (as_of_accuracy is not None) and as_of_accuracy >= float(targets.get("min_as_of_accuracy") or 1.0),
        "supersession_respect": (supersession_rate is not None)
        and supersession_rate >= float(targets.get("min_supersession_respect_rate") or 1.0),
        "contradiction_surfaced": (contradiction_rate is not None)
        and contradiction_rate >= float(targets.get("min_contradiction_surfaced_rate") or 1.0),
    }

    flags = BenchmarkShortcutFlags().to_dict()
    return {
        "schema_version": T3_REPORT_SCHEMA,
        "task_id": "t3_temporal_state_selection",
        "capability": "C3_temporal_as_of_C4_contradiction",
        "case_id": str(fixture.get("id") or "temporal_state_fixture"),
        "description": str(fixture.get("description") or ""),
        "generated_from": str(fixture_path or default_fixture_path()),
        "metadata": {
            "runner": "causal_continuity.t3",
            "commit": _repo_commit(),
            "faithfulness": flags,
            "shortcut_flags": flags,
            "notes": [
                "locomo_like_bucket_reframe",
                "state_selection_not_answer_token_f1",
                "resolver_as_of_and_claim_update_semantics",
            ],
        },
        "targets": targets,
        "metrics": {
            "case_count": int(total),
            "pass_count": int(passed),
            "correct_state_selection_rate": correct_state_rate,
            "as_of_case_count": int(as_of_cases),
            "as_of_accuracy": as_of_accuracy,
            "supersession_case_count": int(supersession_cases),
            "supersession_respect_rate": supersession_rate,
            "contradiction_case_count": int(contradiction_cases),
            "contradiction_surfaced_rate": contradiction_rate,
        },
        "checks": checks,
        "pass": all(checks.values()),
        "cases": sorted(case_rows, key=lambda r: str(r.get("case_id") or "")),
        "latency_ms": round((time.perf_counter() - t0) * 1000.0, 3),
    }


__all__ = [
    "T3_FIXTURE_SCHEMA",
    "T3_REPORT_SCHEMA",
    "default_fixture_path",
    "run_t3_temporal_state",
]
