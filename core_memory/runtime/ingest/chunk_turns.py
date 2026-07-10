"""Versioned document/media chunk turns for owned-ingestion hydration."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.identifiers import validate_archive_id
from core_memory.persistence.io_utils import store_lock
from core_memory.persistence.semantic_lifecycle import mark_semantic_dirty
from core_memory.persistence.turn_archive import append_turn_record, find_turn_record

CHUNK_TURN_SCHEMA = "chunk_turn_record.v1"
CHUNK_TURN_CONTRACT = "memory.chunk_turns.v1"
MAX_CHUNK_TURN_BATCH = 500


def _text(value: Any) -> str:
    return str(value or "").strip()


def _required_text(record: dict[str, Any], field: str) -> str:
    value = _text(record.get(field))
    if not value:
        raise ValueError(f"chunk_turns: missing {field}")
    return value


def _positive_int(record: dict[str, Any], field: str, *, minimum: int) -> int:
    try:
        value = int(record.get(field))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"chunk_turns: invalid {field}") from exc
    if value < minimum:
        raise ValueError(f"chunk_turns: invalid {field}")
    return value


def _clean_string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"chunk_turns: invalid {field}")
    result = [_text(item) for item in value]
    if any(not item for item in result):
        raise ValueError(f"chunk_turns: invalid {field}")
    return list(dict.fromkeys(result))


def _normalize_hydration_ref(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("chunk_turns: invalid hydration_ref")
    hydration_ref = dict(value)
    if _text(hydration_ref.get("schema")) != "hydration_ref.v2":
        raise ValueError("chunk_turns: hydration_ref must use hydration_ref.v2")
    try:
        version = int(hydration_ref.get("version") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("chunk_turns: hydration_ref version must be 2") from exc
    if version != 2:
        raise ValueError("chunk_turns: hydration_ref version must be 2")
    target = hydration_ref.get("target")
    if not isinstance(target, dict) or not _text(target.get("core_memory_unifying_id")):
        raise ValueError("chunk_turns: hydration_ref.target.core_memory_unifying_id is required")
    return hydration_ref


def normalize_chunk_turn(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("chunk_turns: every record must be an object")
    if _text(record.get("schema")) != CHUNK_TURN_SCHEMA:
        raise ValueError(f"chunk_turns: schema must be {CHUNK_TURN_SCHEMA}")

    chunk_id = validate_archive_id(_required_text(record, "chunk_id"), field="chunk_id")
    workspace_id = _required_text(record, "workspace_id")
    source_document_id = _required_text(record, "source_document_id")
    section_id = _text(record.get("section_id"))
    content_text = _required_text(record, "content_text")
    content_hash = _required_text(record, "content_hash")
    chunk_index = _positive_int(record, "chunk_index", minimum=0)
    chunk_set_version = _positive_int(record, "chunk_set_version", minimum=1)
    source_element_ids = _clean_string_list(record.get("source_element_ids"), field="source_element_ids")
    hydration_ref = _normalize_hydration_ref(record.get("hydration_ref"))
    metadata = record.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("chunk_turns: metadata must be an object")

    core_memory_unifying_id = _text((hydration_ref.get("target") or {}).get("core_memory_unifying_id"))
    return {
        "schema": CHUNK_TURN_SCHEMA,
        "workspace_id": workspace_id,
        "source_document_id": source_document_id,
        "section_id": section_id or None,
        "chunk_id": chunk_id,
        "chunk_index": chunk_index,
        "content_text": content_text,
        "content_hash": content_hash,
        "source_element_ids": source_element_ids,
        "chunk_set_version": chunk_set_version,
        "core_memory_unifying_id": core_memory_unifying_id,
        "hydration_ref": hydration_ref,
        "metadata": dict(metadata),
    }


def _chunk_session_id(record: dict[str, Any]) -> str:
    basis = "|".join(
        (
            _text(record.get("workspace_id")),
            _text(record.get("source_document_id")),
            _text(record.get("section_id")) or "root",
            str(int(record.get("chunk_set_version") or 0)),
        )
    )
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]
    return f"chunk-{digest}"


def _identity_hash(value: dict[str, Any]) -> str:
    payload = {
        "workspace_id": _text(value.get("workspace_id")),
        "source_document_id": _text(value.get("source_document_id")),
        "section_id": _text(value.get("section_id")),
        "chunk_index": int(value.get("chunk_index") or 0),
        "content_hash": _text(value.get("content_hash")),
        "source_element_ids": list(value.get("source_element_ids") or []),
        "chunk_set_version": int(value.get("chunk_set_version") or 0),
        "core_memory_unifying_id": _text(value.get("core_memory_unifying_id")),
        "hydration_ref": dict(value.get("hydration_ref") or {}),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _archived_chunk_identity(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return _identity_hash(metadata)


def _persist_chunk_turns(root_path: Path, normalized: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing_by_id: dict[str, dict[str, Any]] = {}
    for record in normalized:
        existing = find_turn_record(root=root_path, turn_id=record["chunk_id"])
        if existing is None:
            continue
        if _archived_chunk_identity(existing) != _identity_hash(record):
            raise ValueError(f"chunk_turns: immutable chunk_id conflict {record['chunk_id']}")
        existing_by_id[record["chunk_id"]] = existing

    receipts: list[dict[str, Any]] = []
    for record in normalized:
        chunk_id = record["chunk_id"]
        session_id = _chunk_session_id(record)
        if chunk_id in existing_by_id:
            receipts.append(
                {
                    "chunk_id": chunk_id,
                    "status": "already_exists",
                    "session_id": _text(existing_by_id[chunk_id].get("session_id")),
                    "chunk_set_version": record["chunk_set_version"],
                }
            )
            continue

        content_text = record["content_text"]
        metadata = {
            **dict(record.get("metadata") or {}),
            "unit": "chunk",
            "workspace_id": record["workspace_id"],
            "source_document_id": record["source_document_id"],
            "section_id": record.get("section_id"),
            "chunk_index": record["chunk_index"],
            "content_hash": record["content_hash"],
            "source_element_ids": list(record["source_element_ids"]),
            "chunk_set_version": record["chunk_set_version"],
            "core_memory_unifying_id": record["core_memory_unifying_id"],
            "hydration_ref": dict(record["hydration_ref"]),
        }
        append_turn_record(
            root=root_path,
            session_id=session_id,
            turn_id=chunk_id,
            transaction_id=f"chunk-{chunk_id}",
            trace_id=f"chunk-{chunk_id}",
            origin="SOURCE_CHUNK",
            ts=_text(metadata.get("recorded_at")) or datetime.now(timezone.utc).isoformat(),
            user_query="",
            assistant_final=content_text,
            turns=[
                {
                    "speaker": "source",
                    "role": "document_chunk",
                    "content": content_text,
                }
            ],
            speakers=["source"],
            assistant_final_ref=None,
            assistant_final_hash=record["content_hash"],
            tools_trace=[],
            mesh_trace=[],
            metadata=metadata,
        )
        receipts.append(
            {
                "chunk_id": chunk_id,
                "status": "accepted",
                "session_id": session_id,
                "chunk_set_version": record["chunk_set_version"],
            }
        )
    return receipts


def ingest_chunk_turns(root: str | Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate and append an idempotent batch of canonical chunk turn records."""
    if not isinstance(records, list) or not records:
        raise ValueError("chunk_turns: records must be a non-empty list")
    if len(records) > MAX_CHUNK_TURN_BATCH:
        raise ValueError(f"chunk_turns: batch exceeds {MAX_CHUNK_TURN_BATCH} records")

    normalized: list[dict[str, Any]] = []
    batch_by_id: dict[str, dict[str, Any]] = {}
    for raw in records:
        record = normalize_chunk_turn(raw)
        chunk_id = record["chunk_id"]
        previous = batch_by_id.get(chunk_id)
        if previous is not None:
            if _identity_hash(previous) != _identity_hash(record):
                raise ValueError(f"chunk_turns: conflicting duplicate chunk_id {chunk_id}")
            continue
        batch_by_id[chunk_id] = record
        normalized.append(record)

    normalized.sort(key=lambda row: (_chunk_session_id(row), int(row["chunk_index"])))
    position_ids: set[tuple[str, int]] = set()
    for record in normalized:
        position_id = (_chunk_session_id(record), int(record["chunk_index"]))
        if position_id in position_ids:
            raise ValueError("chunk_turns: duplicate chunk_index within one section and version")
        position_ids.add(position_id)

    root_path = Path(root)
    with store_lock(root_path):
        receipts = _persist_chunk_turns(root_path, normalized)

    created_count = sum(1 for row in receipts if row["status"] == "accepted")
    if created_count:
        mark_semantic_dirty(root_path, reason="ingest_chunk_turns")

    return {
        "ok": True,
        "accepted": True,
        "contract": CHUNK_TURN_CONTRACT,
        "created_count": created_count,
        "existing_count": sum(1 for row in receipts if row["status"] == "already_exists"),
        "receipts": receipts,
    }


def list_chunk_turns(
    root: str | Path,
    *,
    core_memory_unifying_id: str,
    chunk_set_version_lte: int | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """List chunk records for version-aware GC planning without mutating storage."""
    unifying_id = _text(core_memory_unifying_id)
    if not unifying_id:
        raise ValueError("chunk_turns: core_memory_unifying_id is required")
    version_lte = None
    if chunk_set_version_lte is not None:
        try:
            version_lte = int(chunk_set_version_lte)
        except (TypeError, ValueError) as exc:
            raise ValueError("chunk_turns: invalid chunk_set_version_lte") from exc
        if version_lte < 1:
            raise ValueError("chunk_turns: invalid chunk_set_version_lte")

    max_rows = max(1, min(int(limit or 500), 2000))
    rows: list[dict[str, Any]] = []
    turns_dir = Path(root) / ".turns"
    for turns_file in sorted(turns_dir.glob("session-*.jsonl")):
        with turns_file.open(encoding="utf-8") as handle:
            for line in handle:
                if len(rows) >= max_rows:
                    break
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                metadata = row.get("metadata")
                if not isinstance(metadata, dict) or metadata.get("unit") != "chunk":
                    continue
                if _text(metadata.get("core_memory_unifying_id")) != unifying_id:
                    continue
                try:
                    version = int(metadata.get("chunk_set_version") or 0)
                    chunk_index = int(metadata.get("chunk_index") or 0)
                except (TypeError, ValueError):
                    continue
                if version_lte is not None and version > version_lte:
                    continue
                rows.append(
                    {
                        "chunk_id": _text(row.get("turn_id")),
                        "session_id": _text(row.get("session_id")),
                        "source_document_id": _text(metadata.get("source_document_id")),
                        "section_id": _text(metadata.get("section_id")) or None,
                        "chunk_index": chunk_index,
                        "content_hash": _text(metadata.get("content_hash")),
                        "chunk_set_version": version,
                        "core_memory_unifying_id": unifying_id,
                    }
                )
        if len(rows) >= max_rows:
            break
    rows.sort(key=lambda row: (int(row["chunk_set_version"]), int(row["chunk_index"])))
    return {
        "ok": True,
        "contract": CHUNK_TURN_CONTRACT,
        "core_memory_unifying_id": unifying_id,
        "chunk_set_version_lte": version_lte,
        "count": len(rows),
        "chunks": rows,
    }


__all__ = [
    "CHUNK_TURN_CONTRACT",
    "CHUNK_TURN_SCHEMA",
    "ingest_chunk_turns",
    "list_chunk_turns",
    "normalize_chunk_turn",
]
