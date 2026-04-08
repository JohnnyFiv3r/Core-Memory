from __future__ import annotations

from typing import Any, Optional

from core_memory.runtime.session_surface import read_session_surface


def query_for_store(
    store: Any,
    *,
    type: Optional[str] = None,
    status: Optional[str] = None,
    tags: Optional[list] = None,
    scope: Optional[str] = None,
    limit: int = 20,
    session_id: Optional[str] = None,
) -> list:
    """Query beads with filters."""
    from core_memory.schema.models import BeadType, Status, Scope

    type_filter = store._normalize_enum(type, BeadType)
    status_filter = store._normalize_enum(status, Status)
    scope_filter = store._normalize_enum(scope, Scope)

    results = []

    if session_id:
        source_rows = list(read_session_surface(store.root, session_id))
        if not source_rows:
            index = store._read_json(store.beads_dir / "index.json")
            source_rows = [
                b
                for b in (index.get("beads") or {}).values()
                if str((b or {}).get("session_id") or "") == str(session_id)
            ]
        iterable = source_rows
    else:
        index = store._read_json(store.beads_dir / "index.json")
        iterable = list((index.get("beads") or {}).values())

    def bead_matches_status(bead: dict, filt: str | None) -> bool:
        if not filt:
            return True
        f = str(filt or "").strip().lower()
        s = str(bead.get("status") or "").strip().lower()
        p = str(bead.get("promotion_state") or "").strip().lower()
        # storage-axis canonical
        if f == "default":
            return s in {"default", "open", "candidate", "promoted"}
        # promotion-axis compatibility filters
        if f in {"candidate", "promoted"}:
            return p == f or s == f
        # legacy open maps to default active storage state
        if f == "open":
            return s in {"default", "open"} and p not in {"candidate", "promoted"}
        # archived/superseded and any future exact status
        return s == f

    for bead in iterable:
        if type_filter and bead.get("type") != type_filter:
            continue
        if not bead_matches_status(bead, status_filter):
            continue
        if scope_filter and bead.get("scope") != scope_filter:
            continue
        if tags:
            bead_tags = set(bead.get("tags", []))
            if not bead_tags.intersection(set(tags)):
                continue
        results.append(bead)

        if len(results) >= limit:
            break

    return results


__all__ = ["query_for_store"]
