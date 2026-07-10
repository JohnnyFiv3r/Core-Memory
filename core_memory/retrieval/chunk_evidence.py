"""Semantic evidence rows backed by owned-ingestion chunk turns.

Chunks remain turn-archive records.  This module only projects chunks that are
explicitly cited by a visible document section bead into the semantic index,
then resolves vector hits back to that parent bead before retrieval consumers
can treat the result as a graph anchor.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CHUNK_EVIDENCE_UNIT = "chunk_evidence"
CHUNK_EVIDENCE_ANCHOR_REASON = "chunk_evidence_resolve_up"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _section_ids(bead: dict[str, Any]) -> set[str]:
    section_ids: set[str] = set()
    for ref in bead.get("section_refs") or []:
        if isinstance(ref, dict):
            section_id = _text(ref.get("section_id"))
            if section_id:
                section_ids.add(section_id)
    hydration_ref = bead.get("hydration_ref")
    if isinstance(hydration_ref, dict):
        target = hydration_ref.get("target")
        if isinstance(target, dict):
            section_id = _text(target.get("section_id"))
            if section_id:
                section_ids.add(section_id)
    return section_ids


def _is_document_section_parent(bead: dict[str, Any]) -> bool:
    if not list(bead.get("source_turn_ids") or []):
        return False
    if not _section_ids(bead):
        return False
    bead_type = _text(bead.get("type")).lower()
    source_kind = _text(bead.get("source_kind")).lower()
    data_type_flag = _text(bead.get("data_type_flag")).lower()
    return (
        bead_type in {"document_reference", "document_section"}
        or source_kind in {"document", "media"}
        or data_type_flag.startswith("document")
    )


def _vector_id(parent_bead_id: str, chunk_id: str) -> str:
    digest = hashlib.sha256(f"{parent_bead_id}|{chunk_id}".encode("utf-8")).hexdigest()[:40]
    return f"chunk-evidence-{digest}"


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _referenced_turn_records(root: Path, turn_ids: set[str]) -> list[dict[str, Any]]:
    """Read only cited turn records through the archive's offset indexes."""
    remaining = set(turn_ids)
    records: list[dict[str, Any]] = []
    turns_dir = root / ".turns"
    for index_file in sorted(turns_dir.glob("session-*.idx.json")):
        if not remaining:
            break
        try:
            index = json.loads(index_file.read_text(encoding="utf-8"))
        except (OSError, TypeError, json.JSONDecodeError):
            continue
        if not isinstance(index, dict):
            continue
        hits = sorted(remaining.intersection(_text(key) for key in index.keys()))
        if not hits:
            continue
        turns_file = index_file.with_name(index_file.name.removesuffix(".idx.json") + ".jsonl")
        if not turns_file.exists():
            continue
        try:
            with turns_file.open("rb") as handle:
                for turn_id in hits:
                    location = index.get(turn_id) if isinstance(index.get(turn_id), dict) else {}
                    offset = _integer(location.get("offset"))
                    length = _integer(location.get("length"))
                    if length < 1:
                        continue
                    handle.seek(offset)
                    raw = handle.read(length)
                    try:
                        turn = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, TypeError, json.JSONDecodeError):
                        continue
                    if isinstance(turn, dict) and _text(turn.get("turn_id")) == turn_id:
                        records.append(turn)
                        remaining.discard(turn_id)
        except OSError:
            continue
    return records


def build_chunk_evidence_corpus(
    root: str | Path,
    *,
    visible_bead_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project cited chunk turns into semantic-only evidence rows.

    Orphan chunks and cross-document citations fail closed.  A row is emitted
    only when a visible document section bead cites the exact chunk turn, the
    section IDs agree, and both records carry the same unifying ID.
    """
    parents_by_turn: dict[str, list[dict[str, Any]]] = {}
    for row in visible_bead_rows:
        bead = row.get("bead") if isinstance(row.get("bead"), dict) else {}
        if not _is_document_section_parent(bead):
            continue
        parent_id = _text(row.get("bead_id") or bead.get("id"))
        parent_unifying_id = _text(bead.get("core_memory_unifying_id"))
        if not parent_id or not parent_unifying_id:
            continue
        parent = {
            "bead_id": parent_id,
            "status": _text(row.get("status") or bead.get("status")),
            "session_id": _text(row.get("session_id") or bead.get("session_id")),
            "created_at": _text(row.get("created_at") or bead.get("created_at")),
            "incident_id": _text(row.get("incident_id") or bead.get("incident_id")),
            "tags": list(row.get("tags") or bead.get("tags") or []),
            "section_ids": _section_ids(bead),
            "core_memory_unifying_id": parent_unifying_id,
        }
        for turn_id in dict.fromkeys(_text(value) for value in bead.get("source_turn_ids") or []):
            if turn_id:
                parents_by_turn.setdefault(turn_id, []).append(parent)

    if not parents_by_turn:
        return []

    rows: list[dict[str, Any]] = []
    for turn in _referenced_turn_records(Path(root), set(parents_by_turn.keys())):
        chunk_id = _text(turn.get("turn_id"))
        parents = parents_by_turn.get(chunk_id) or []
        metadata = turn.get("metadata") if isinstance(turn.get("metadata"), dict) else {}
        if _text(metadata.get("unit")).lower() != "chunk":
            continue
        content_text = _text(turn.get("assistant_final") or turn.get("turn_text"))
        chunk_unifying_id = _text(metadata.get("core_memory_unifying_id"))
        section_id = _text(metadata.get("section_id"))
        if not content_text or not chunk_unifying_id or not section_id:
            continue
        for parent in parents:
            if chunk_unifying_id != parent["core_memory_unifying_id"]:
                continue
            if section_id not in parent["section_ids"]:
                continue
            parent_id = _text(parent.get("bead_id"))
            rows.append(
                {
                    "bead_id": _vector_id(parent_id, chunk_id),
                    "parent_bead_id": parent_id,
                    "evidence_turn_id": chunk_id,
                    "unit": CHUNK_EVIDENCE_UNIT,
                    "status": parent.get("status"),
                    "source_surface": CHUNK_EVIDENCE_UNIT,
                    "session_id": parent.get("session_id"),
                    "created_at": _text(turn.get("ts")) or parent.get("created_at"),
                    "incident_id": parent.get("incident_id"),
                    "tags": list(parent.get("tags") or []),
                    "semantic_text": content_text,
                    "lexical_text": content_text,
                    "source_document_id": _text(metadata.get("source_document_id")),
                    "section_id": section_id,
                    "chunk_set_version": _integer(metadata.get("chunk_set_version")),
                    "core_memory_unifying_id": chunk_unifying_id,
                }
            )

    rows.sort(key=lambda row: (_text(row.get("parent_bead_id")), _text(row.get("evidence_turn_id"))))
    return rows


def resolve_semantic_hits(
    hits: list[dict[str, Any]],
    *,
    row_by_id: dict[str, dict[str, Any]] | None = None,
    k: int | None = None,
    include_metadata: bool = False,
) -> list[dict[str, Any]]:
    """Resolve semantic-only evidence IDs to visible parent bead IDs.

    Multiple chunk hits for one section are deduplicated to the highest-scoring
    parent result while retaining compact evidence provenance for diagnostics.
    """
    rows_provided = row_by_id is not None
    rows = row_by_id or {}
    resolved_by_parent: dict[str, dict[str, Any]] = {}
    for raw in hits:
        if not isinstance(raw, dict):
            continue
        vector_id = _text(raw.get("bead_id"))
        if not vector_id:
            continue
        if rows_provided and vector_id not in rows:
            continue
        row = rows.get(vector_id) if isinstance(rows.get(vector_id), dict) else {}
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        context = {**row, **metadata}
        parent_id = _text(context.get("parent_bead_id")) or vector_id
        evidence_turn_id = _text(context.get("evidence_turn_id"))
        is_evidence = _text(context.get("unit")) == CHUNK_EVIDENCE_UNIT and parent_id != vector_id
        score = float(raw.get("score") or 0.0)

        candidate = dict(raw)
        if not include_metadata:
            candidate.pop("metadata", None)
        candidate["bead_id"] = parent_id
        candidate["score"] = score
        candidate["status"] = raw.get("status") or context.get("status")
        candidate["anchor_reason"] = (
            CHUNK_EVIDENCE_ANCHOR_REASON if is_evidence else _text(raw.get("anchor_reason")) or "retrieved"
        )
        if is_evidence:
            candidate["evidence_turn_ids"] = [evidence_turn_id] if evidence_turn_id else []
            candidate["resolved_from_vector_ids"] = [vector_id]

        existing = resolved_by_parent.get(parent_id)
        if existing is None or score > float(existing.get("score") or 0.0):
            if existing:
                candidate["evidence_turn_ids"] = list(existing.get("evidence_turn_ids") or [])
                candidate["resolved_from_vector_ids"] = list(existing.get("resolved_from_vector_ids") or [])
                if candidate["evidence_turn_ids"]:
                    candidate["anchor_reason"] = CHUNK_EVIDENCE_ANCHOR_REASON
            resolved_by_parent[parent_id] = candidate
            existing = candidate
        if existing is None:
            continue
        if is_evidence:
            existing["anchor_reason"] = CHUNK_EVIDENCE_ANCHOR_REASON
            for key, value in (
                ("evidence_turn_ids", evidence_turn_id),
                ("resolved_from_vector_ids", vector_id),
            ):
                values = list(existing.get(key) or [])
                if value and value not in values:
                    values.append(value)
                existing[key] = values

    out = sorted(
        resolved_by_parent.values(),
        key=lambda row: (-float(row.get("score") or 0.0), _text(row.get("bead_id"))),
    )
    return out[: max(1, int(k))] if k is not None else out


__all__ = [
    "CHUNK_EVIDENCE_ANCHOR_REASON",
    "CHUNK_EVIDENCE_UNIT",
    "build_chunk_evidence_corpus",
    "resolve_semantic_hits",
]
