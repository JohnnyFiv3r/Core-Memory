"""Read-only audit for canonical and legacy persisted event schema rows."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from core_memory.schema.event_schemas import (
    CRAWLER_UPDATE,
    CRAWLER_UPDATE_LEGACY,
    FLUSH_CHECKPOINT,
    FLUSH_CHECKPOINT_LEGACY,
    FLUSH_REPORT,
    FLUSH_REPORT_LEGACY,
    HEALTH_REPORT,
    HEALTH_REPORT_LEGACY,
    MEMORY_EVENT,
    MEMORY_EVENT_LEGACY,
    TURN_ENVELOPE,
    TURN_ENVELOPE_LEGACY,
)


EVENT_SCHEMA_AUDIT_SCHEMA = "core_memory.event_schema_audit.v1"

_SCHEMA_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("crawler_update", CRAWLER_UPDATE, CRAWLER_UPDATE_LEGACY),
    ("flush_checkpoint", FLUSH_CHECKPOINT, FLUSH_CHECKPOINT_LEGACY),
    ("flush_report", FLUSH_REPORT, FLUSH_REPORT_LEGACY),
    ("health_report", HEALTH_REPORT, HEALTH_REPORT_LEGACY),
    ("memory_event", MEMORY_EVENT, MEMORY_EVENT_LEGACY),
    ("turn_envelope", TURN_ENVELOPE, TURN_ENVELOPE_LEGACY),
)

_KIND_BY_SCHEMA: dict[str, str] = {}
_CANONICAL_SCHEMAS: set[str] = set()
_LEGACY_SCHEMAS: set[str] = set()
for _kind, _canonical, _legacy in _SCHEMA_PAIRS:
    _KIND_BY_SCHEMA[_canonical] = _kind
    _KIND_BY_SCHEMA[_legacy] = _kind
    _CANONICAL_SCHEMAS.add(_canonical)
    _LEGACY_SCHEMAS.add(_legacy)


def _events_dir(root: Path) -> Path:
    return root / ".beads" / "events"


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _bump(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def _iter_schema_values(value: Any, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "schema" and isinstance(child, str) and child.strip():
                yield child_path, child.strip()
            if isinstance(child, (dict, list)):
                yield from _iter_schema_values(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            if isinstance(child, (dict, list)):
                yield from _iter_schema_values(child, child_path)


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _row_context(row: dict[str, Any]) -> dict[str, str]:
    event = row.get("event") if isinstance(row.get("event"), dict) else {}
    envelope = row.get("envelope") if isinstance(row.get("envelope"), dict) else {}
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return {
        "id": _first_text(row.get("id"), row.get("event_id"), event.get("event_id"), envelope.get("event_id")),
        "session_id": _first_text(row.get("session_id"), event.get("session_id"), envelope.get("session_id"), payload.get("session_id")),
        "turn_id": _first_text(row.get("turn_id"), event.get("turn_id"), envelope.get("turn_id"), payload.get("turn_id")),
        "stage": _first_text(row.get("stage"), payload.get("stage")),
        "event_type": _first_text(row.get("event_type"), payload.get("event_type")),
    }


def _sample_row(
    *,
    root: Path,
    path: Path,
    line: int,
    field_path: str,
    schema: str,
    classification: str,
    row: dict[str, Any],
) -> dict[str, Any]:
    out = {
        "file": _relative(path, root),
        "line": int(line),
        "field_path": field_path,
        "schema": schema,
        "classification": classification,
        "event_kind": _KIND_BY_SCHEMA.get(schema, ""),
    }
    for key, value in _row_context(row).items():
        if value:
            out[key] = value
    return out


def audit_event_schemas(root: str | Path, *, limit: int = 100) -> dict[str, Any]:
    """Report canonical and legacy event schema rows without mutating the store."""
    root_path = Path(root)
    events_dir = _events_dir(root_path)
    sample_limit = max(0, int(limit))

    files_scanned: list[str] = []
    rows_scanned = 0
    canonical_rows: list[dict[str, Any]] = []
    legacy_rows: list[dict[str, Any]] = []
    invalid_jsonl_lines: list[dict[str, Any]] = []
    invalid_jsonl_line_count = 0
    canonical_schema_counts: dict[str, int] = {}
    legacy_schema_counts: dict[str, int] = {}
    other_schema_counts: dict[str, int] = {}
    canonical_row_keys: set[tuple[str, int]] = set()
    legacy_row_keys: set[tuple[str, int]] = set()
    canonical_match_count = 0
    legacy_match_count = 0

    jsonl_files = sorted(events_dir.glob("*.jsonl")) if events_dir.exists() else []
    for path in jsonl_files:
        files_scanned.append(_relative(path, root_path))
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            rows_scanned += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                invalid_jsonl_line_count += 1
                if len(invalid_jsonl_lines) < sample_limit:
                    invalid_jsonl_lines.append({"file": _relative(path, root_path), "line": line_no, "error": str(exc)})
                continue
            if not isinstance(row, dict):
                continue
            for field_path, schema in _iter_schema_values(row):
                if schema in _LEGACY_SCHEMAS:
                    legacy_match_count += 1
                    legacy_row_keys.add((_relative(path, root_path), line_no))
                    _bump(legacy_schema_counts, schema)
                    if len(legacy_rows) < sample_limit:
                        legacy_rows.append(
                            _sample_row(
                                root=root_path,
                                path=path,
                                line=line_no,
                                field_path=field_path,
                                schema=schema,
                                classification="legacy",
                                row=row,
                            )
                        )
                elif schema in _CANONICAL_SCHEMAS:
                    canonical_match_count += 1
                    canonical_row_keys.add((_relative(path, root_path), line_no))
                    _bump(canonical_schema_counts, schema)
                    if len(canonical_rows) < sample_limit:
                        canonical_rows.append(
                            _sample_row(
                                root=root_path,
                                path=path,
                                line=line_no,
                                field_path=field_path,
                                schema=schema,
                                classification="canonical",
                                row=row,
                            )
                        )
                else:
                    _bump(other_schema_counts, schema)

    legacy_row_count = len(legacy_row_keys)
    canonical_row_count = len(canonical_row_keys)
    return {
        "ok": True,
        "schema": EVENT_SCHEMA_AUDIT_SCHEMA,
        "root": str(root_path),
        "events_dir": str(events_dir),
        "events_dir_exists": events_dir.exists(),
        "read_only": True,
        "mutation": {"performed": False},
        "files_scanned": len(files_scanned),
        "scanned_files": files_scanned,
        "rows_scanned": rows_scanned,
        "canonical_row_count": canonical_row_count,
        "canonical_match_count": canonical_match_count,
        "legacy_row_count": legacy_row_count,
        "legacy_match_count": legacy_match_count,
        "has_legacy_event_schema_rows": legacy_row_count > 0,
        "canonical_schema_counts": dict(sorted(canonical_schema_counts.items())),
        "legacy_schema_counts": dict(sorted(legacy_schema_counts.items())),
        "other_schema_counts": dict(sorted(other_schema_counts.items())),
        "invalid_jsonl_line_count": invalid_jsonl_line_count,
        "invalid_jsonl_lines": invalid_jsonl_lines,
        "legacy_rows": legacy_rows,
        "canonical_rows": canonical_rows,
        "truncated": {
            "legacy_rows": legacy_match_count > len(legacy_rows),
            "canonical_rows": canonical_match_count > len(canonical_rows),
            "invalid_jsonl_lines": invalid_jsonl_line_count > len(invalid_jsonl_lines),
        },
        "limits": {"row_sample_limit": sample_limit},
    }


__all__ = ["EVENT_SCHEMA_AUDIT_SCHEMA", "audit_event_schemas"]
