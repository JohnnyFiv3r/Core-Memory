from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REQUIRED_FIXTURE_KEYS = {"id", "query", "bucket_labels", "gold_id", "setup"}
REQUIRED_GOLD_KEYS = {"id", "expected_answer_class", "bucket_labels"}
ALLOWED_ANSWER_CLASSES = {"answer_current", "answer_historical", "answer_partial", "abstain"}


@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    query: str
    intent: str
    bucket_labels: tuple[str, ...]
    gold_id: str
    setup: dict[str, Any]
    constraints: dict[str, Any]
    k: int


@dataclass(frozen=True)
class GoldCase:
    id: str
    expected_answer_class: str
    bucket_labels: tuple[str, ...]
    expected_slot: str | None = None
    expected_source_surface: str | None = None


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid_jsonl:{path}:{line_no}:{exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"invalid_jsonl_row_not_object:{path}:{line_no}")
            out.append(row)
    return out


def validate_fixture_row(row: dict[str, Any]) -> tuple[bool, list[str]]:
    errs: list[str] = []
    missing = sorted(REQUIRED_FIXTURE_KEYS - set(row.keys()))
    for k in missing:
        errs.append(f"missing:{k}")

    if "id" in row and not str(row.get("id") or "").strip():
        errs.append("id_empty")
    if "query" in row and not str(row.get("query") or "").strip():
        errs.append("query_empty")

    buckets = row.get("bucket_labels")
    if not isinstance(buckets, list) or not buckets:
        errs.append("bucket_labels_invalid")

    setup = row.get("setup")
    if not isinstance(setup, dict):
        errs.append("setup_invalid")
    else:
        beads = setup.get("beads")
        turns = setup.get("turns")
        beads_ok = isinstance(beads, list) and len(beads) > 0
        turns_ok = isinstance(turns, list) and len(turns) > 0
        if not beads_ok and not turns_ok:
            errs.append("setup_materialization_invalid")

    return (len(errs) == 0, errs)


def validate_gold_row(row: dict[str, Any]) -> tuple[bool, list[str]]:
    errs: list[str] = []
    missing = sorted(REQUIRED_GOLD_KEYS - set(row.keys()))
    for k in missing:
        errs.append(f"missing:{k}")

    answer = str(row.get("expected_answer_class") or "").strip()
    if answer and answer not in ALLOWED_ANSWER_CLASSES:
        errs.append("expected_answer_class_invalid")

    buckets = row.get("bucket_labels")
    if not isinstance(buckets, list) or not buckets:
        errs.append("bucket_labels_invalid")

    return (len(errs) == 0, errs)


def load_fixture_rows(fixtures_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(fixtures_dir.glob("*.jsonl")):
        rows.extend(_read_jsonl(path))
    return rows


def load_gold_rows(gold_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(gold_dir.glob("*.json")):
        payload = _read_json(path)
        if isinstance(payload, list):
            candidates = payload
        elif isinstance(payload, dict) and isinstance(payload.get("cases"), list):
            candidates = payload.get("cases") or []
        else:
            raise ValueError(f"gold_invalid_shape:{path}")
        for row in candidates:
            if not isinstance(row, dict):
                raise ValueError(f"gold_row_not_object:{path}")
            case_id = str(row.get("id") or "").strip()
            if not case_id:
                raise ValueError(f"gold_missing_id:{path}")
            out[case_id] = row
    return out


def build_cases(*, fixtures_dir: Path, gold_dir: Path) -> list[tuple[BenchmarkCase, GoldCase]]:
    fixture_rows = load_fixture_rows(fixtures_dir)
    gold_rows = load_gold_rows(gold_dir)

    cases: list[tuple[BenchmarkCase, GoldCase]] = []
    seen_ids: set[str] = set()

    for row in sorted(fixture_rows, key=lambda r: str(r.get("id") or "")):
        ok, errs = validate_fixture_row(row)
        if not ok:
            raise ValueError(f"fixture_invalid:{row.get('id')}:{','.join(errs)}")

        case_id = str(row.get("id") or "").strip()
        if case_id in seen_ids:
            raise ValueError(f"fixture_duplicate_id:{case_id}")
        seen_ids.add(case_id)

        gold_id = str(row.get("gold_id") or "").strip()
        if gold_id not in gold_rows:
            raise ValueError(f"gold_missing_for_fixture:{case_id}:{gold_id}")

        g_row = gold_rows[gold_id]
        g_ok, g_errs = validate_gold_row(g_row)
        if not g_ok:
            raise ValueError(f"gold_invalid:{gold_id}:{','.join(g_errs)}")

        fx = BenchmarkCase(
            id=case_id,
            query=str(row.get("query") or "").strip(),
            intent=str(row.get("intent") or "remember").strip() or "remember",
            bucket_labels=tuple(sorted(str(x) for x in (row.get("bucket_labels") or []))),
            gold_id=gold_id,
            setup=dict(row.get("setup") or {}),
            constraints=dict(row.get("constraints") or {"require_structural": False}),
            k=max(1, int(row.get("k") or 5)),
        )
        gd = GoldCase(
            id=str(g_row.get("id") or "").strip(),
            expected_answer_class=str(g_row.get("expected_answer_class") or "answer_partial").strip(),
            bucket_labels=tuple(sorted(str(x) for x in (g_row.get("bucket_labels") or []))),
            expected_slot=(str(g_row.get("expected_slot") or "").strip() or None),
            expected_source_surface=(str(g_row.get("expected_source_surface") or "").strip() or None),
        )
        cases.append((fx, gd))

    return cases
