from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core_memory.config.feature_flags import (
    default_adjacent_turns,
    default_hydrate_tools_enabled,
    transcript_hydration_enabled,
)
from core_memory.persistence.turn_archive import (
    find_turn_record,
    get_adjacent_turns,
    get_turn_tools,
)


def hydrate_bead_sources_for_root(
    *,
    root: str | Path,
    bead_ids: list[str] | None = None,
    turn_ids: list[str] | None = None,
    include_tools: bool | None = None,
    before: int | None = None,
    after: int | None = None,
) -> dict[str, Any]:
    """Hydrate turn records from bead provenance links and/or explicit turn IDs."""
    if not transcript_hydration_enabled():
        return {
            "schema": "core_memory.hydrate_bead_sources.v1",
            "disabled": True,
            "reason": "transcript_hydration_disabled",
            "beads": [],
            "requested_turn_ids": [],
            "hydrated": [],
        }

    include_tools_final = bool(default_hydrate_tools_enabled() if include_tools is None else include_tools)
    before_final = default_adjacent_turns() if before is None else max(0, int(before or 0))
    after_final = default_adjacent_turns() if after is None else max(0, int(after or 0))

    root_path = Path(root)
    requested_bead_ids = [str(x).strip() for x in (bead_ids or []) if str(x).strip()]
    requested_turn_ids = [str(x).strip() for x in (turn_ids or []) if str(x).strip()]

    resolved_turn_ids: list[str] = []
    bead_rows: list[dict[str, Any]] = []

    if requested_bead_ids:
        idx_path = root_path / ".beads" / "index.json"
        if idx_path.exists():
            try:
                idx = json.loads(idx_path.read_text(encoding="utf-8"))
            except Exception:
                idx = {}
            beads_map = (idx or {}).get("beads") or {}
            for bead_id in requested_bead_ids:
                bead = beads_map.get(bead_id)
                if not isinstance(bead, dict):
                    continue
                source_turn_ids = list(bead.get("source_turn_ids") or [])
                bead_rows.append(
                    {
                        "id": bead_id,
                        "session_id": bead.get("session_id"),
                        "source_turn_ids": source_turn_ids,
                    }
                )
                for turn_id in source_turn_ids:
                    turn_id_str = str(turn_id).strip()
                    if turn_id_str:
                        resolved_turn_ids.append(turn_id_str)

    resolved_turn_ids.extend(requested_turn_ids)
    seen: set[str] = set()
    uniq_turn_ids: list[str] = []
    for turn_id in resolved_turn_ids:
        if turn_id in seen:
            continue
        seen.add(turn_id)
        uniq_turn_ids.append(turn_id)

    hydrated_turns: list[dict[str, Any]] = []
    for turn_id in uniq_turn_ids:
        row = find_turn_record(root=root_path, turn_id=turn_id)
        if not row:
            continue
        session_id = row.get("session_id")
        entry: dict[str, Any] = {"turn": row}
        if include_tools_final:
            entry["tools"] = get_turn_tools(root=root_path, turn_id=turn_id, session_id=session_id)
        if before_final or after_final:
            entry["adjacent"] = get_adjacent_turns(
                root=root_path,
                turn_id=turn_id,
                session_id=session_id,
                before=before_final,
                after=after_final,
            )
        hydrated_turns.append(entry)

    return {
        "schema": "core_memory.hydrate_bead_sources.v1",
        "beads": bead_rows,
        "requested_turn_ids": uniq_turn_ids,
        "hydrated": hydrated_turns,
    }


__all__ = ["hydrate_bead_sources_for_root"]
