"""Ingest path for external data insight beads.

Converts a `core_memory_insights` row from PipeHouse into a turn envelope
and calls `emit_turn_finalized()`. Never writes to the bead store directly.
"""
from __future__ import annotations

from typing import Any

from core_memory.runtime.engine import emit_turn_finalized

_REQUIRED_FIELDS = ("id", "source_table", "as_of_timestamp", "entity_refs", "attribute_tags", "title", "content")


def _validate_row(row: dict[str, Any]) -> None:
    missing = [f for f in _REQUIRED_FIELDS if not str(row.get(f) or "").strip() and row.get(f) != 0]
    if missing:
        raise ValueError(f"data_insight row missing required fields: {missing}")


def ingest_data_insight_row(root: str, session_id: str, row: dict[str, Any]) -> dict[str, Any]:
    """Convert a core_memory_insights row to a turn envelope and emit it.

    Returns ``{"ok": True, "bead_id": "<id>"}`` on success.
    Raises ``ValueError`` for missing required fields so callers get explicit
    errors rather than silent empty beads.
    """
    _validate_row(row)

    record_id = str(row["id"])
    turn_id = f"data-insight-{record_id}"

    links: dict[str, Any] = {"external_source_id": record_id}
    unifying_id = str(row.get("core_memory_unifying_id") or "").strip()
    if unifying_id:
        links["core_memory_unifying_id"] = unifying_id

    turn_metadata: dict[str, Any] = {
        "type": "data_insight",
        "source_system": "pipehouse",
        "source_table": str(row["source_table"]),
        "source_record_id": record_id,
        "as_of_timestamp": str(row["as_of_timestamp"]),
        "entity_refs": list(row.get("entity_refs") or []),
        "attribute_tags": list(row.get("attribute_tags") or []),
        "title": str(row["title"])[:120],
        "links": links,
    }
    if row.get("confidence") is not None:
        turn_metadata["confidence"] = float(row["confidence"])
    if row.get("because"):
        turn_metadata["because"] = list(row["because"])
    if row.get("pipehouse_metadata"):
        turn_metadata["pipehouse_metadata"] = dict(row["pipehouse_metadata"])

    result = emit_turn_finalized(
        root=root,
        session_id=session_id,
        turn_id=turn_id,
        turns=[{
            "speaker": "pipehouse",
            "role": "other",
            "content": str(row["content"]),
            "metadata": turn_metadata,
        }],
        origin="pipehouse",
    )

    bead_id = str((result or {}).get("bead_id") or "")
    return {"ok": True, "bead_id": bead_id, "turn_id": turn_id, "raw": result}
