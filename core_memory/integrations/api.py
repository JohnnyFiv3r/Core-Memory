from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from core_memory.runtime.ingress import maybe_emit_finalize_memory_event
from core_memory.runtime.turn_archive import find_turn_record, get_turn_tools as _get_turn_tools, get_adjacent_turns as _get_adjacent_turns
from core_memory.integrations.openclaw_flags import transcript_hydration_enabled, default_hydrate_tools_enabled, default_adjacent_turns
from core_memory.persistence.store import DEFAULT_ROOT


@dataclass
class IntegrationContext:
    framework: str
    source: str
    store_full_text: bool = True
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    thread_id: Optional[str] = None

    def to_metadata(self) -> dict[str, Any]:
        md = asdict(self)
        return {k: v for k, v in md.items() if v is not None}


def _resolve_root(root: Optional[str]) -> str:
    return (root or os.environ.get("CORE_MEMORY_ROOT") or DEFAULT_ROOT).strip()


def emit_turn_finalized(
    *,
    root: Optional[str] = None,
    session_id: str,
    turn_id: str,
    transaction_id: str,
    user_query: str,
    assistant_final: str,
    trace_id: Optional[str] = None,
    trace_depth: int = 0,
    origin: str = "USER_TURN",
    tools_trace: Optional[list[dict]] = None,
    mesh_trace: Optional[list[dict]] = None,
    window_turn_ids: Optional[list[str]] = None,
    window_bead_ids: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
    strict: bool = False,
) -> Optional[str]:
    """Stable integration port for external orchestrators.

    Emits one TURN_FINALIZED memory event for a top-level turn.
    - strict=False (default): return None when emission is skipped
    - strict=True: raise ValueError when skipped
    """
    root_final = _resolve_root(root)
    trace_id_final = (trace_id or f"tr-{turn_id}-{uuid.uuid4().hex[:8]}").strip()
    result = maybe_emit_finalize_memory_event(
        root_final,
        session_id=session_id,
        turn_id=turn_id,
        transaction_id=transaction_id,
        trace_id=trace_id_final,
        user_query=user_query,
        assistant_final=assistant_final,
        trace_depth=trace_depth,
        origin=origin,
        tools_trace=tools_trace,
        mesh_trace=mesh_trace,
        window_turn_ids=window_turn_ids,
        window_bead_ids=window_bead_ids,
        metadata=metadata,
    )
    if not result.get("emitted"):
        if strict:
            raise ValueError(f"turn not emitted: {result.get('reason')}")
        return None
    event_id = result.get("event_id")
    if not event_id:
        if strict:
            raise ValueError("missing event_id after emit")
        return None
    return str(event_id)


def emit_turn_finalized_from_envelope(*, root: Optional[str] = None, envelope: dict[str, Any], strict: bool = False) -> Optional[str]:
    return emit_turn_finalized(
        root=root,
        session_id=str(envelope.get("session_id") or "main"),
        turn_id=str(envelope.get("turn_id") or uuid.uuid4().hex[:12]),
        transaction_id=str(envelope.get("transaction_id") or f"tx-{uuid.uuid4().hex[:12]}"),
        trace_id=str(envelope.get("trace_id") or f"tr-{uuid.uuid4().hex[:12]}"),
        user_query=str(envelope.get("user_query") or ""),
        assistant_final=str(envelope.get("assistant_final") or ""),
        trace_depth=int(envelope.get("trace_depth", 0) or 0),
        origin=str(envelope.get("origin") or "USER_TURN"),
        tools_trace=envelope.get("tools_trace") or [],
        mesh_trace=envelope.get("mesh_trace") or [],
        window_turn_ids=envelope.get("window_turn_ids") or [],
        window_bead_ids=envelope.get("window_bead_ids") or [],
        metadata=envelope.get("metadata") or {},
        strict=strict,
    )


def get_turn(
    *,
    turn_id: str,
    root: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Retrieve authoritative turn record from transcript archive.

    - If session_id is provided, lookup is direct in that session index.
    - Otherwise, search all per-session indexes under `.turns/`.
    """
    root_final = _resolve_root(root)
    if not transcript_hydration_enabled():
        return None
    tid = str(turn_id or "").strip()
    if not tid:
        return None
    sid = str(session_id or "").strip() or None
    return find_turn_record(root=Path(root_final), turn_id=tid, session_id=sid)


def get_turn_tools(
    *,
    turn_id: str,
    root: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    root_final = _resolve_root(root)
    if not transcript_hydration_enabled():
        return None
    tid = str(turn_id or "").strip()
    if not tid:
        return None
    sid = str(session_id or "").strip() or None
    return _get_turn_tools(root=Path(root_final), turn_id=tid, session_id=sid)


def get_adjacent_turns(
    *,
    turn_id: str,
    root: Optional[str] = None,
    session_id: Optional[str] = None,
    before: int = 1,
    after: int = 1,
) -> Optional[dict[str, Any]]:
    root_final = _resolve_root(root)
    if not transcript_hydration_enabled():
        return None
    tid = str(turn_id or "").strip()
    if not tid:
        return None
    sid = str(session_id or "").strip() or None
    return _get_adjacent_turns(
        root=Path(root_final),
        turn_id=tid,
        session_id=sid,
        before=max(0, int(before or 0)),
        after=max(0, int(after or 0)),
    )


def hydrate_bead_sources(
    *,
    root: Optional[str] = None,
    bead_ids: Optional[list[str]] = None,
    turn_ids: Optional[list[str]] = None,
    include_tools: bool | None = None,
    before: int | None = None,
    after: int | None = None,
) -> dict[str, Any]:
    """Hydrate turn records from bead provenance links and/or explicit turn IDs."""
    root_final = _resolve_root(root)
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

    root_path = Path(root_final)

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
            for bid in requested_bead_ids:
                b = beads_map.get(bid)
                if not isinstance(b, dict):
                    continue
                bead_rows.append({"id": bid, "session_id": b.get("session_id"), "source_turn_ids": list(b.get("source_turn_ids") or [])})
                for tid in (b.get("source_turn_ids") or []):
                    t = str(tid).strip()
                    if t:
                        resolved_turn_ids.append(t)

    resolved_turn_ids.extend(requested_turn_ids)
    seen: set[str] = set()
    uniq_turn_ids: list[str] = []
    for tid in resolved_turn_ids:
        if tid in seen:
            continue
        seen.add(tid)
        uniq_turn_ids.append(tid)

    hydrated_turns: list[dict[str, Any]] = []
    for tid in uniq_turn_ids:
        row = get_turn(turn_id=tid, root=root_final)
        if not row:
            continue
        entry: dict[str, Any] = {"turn": row}
        if include_tools_final:
            entry["tools"] = get_turn_tools(turn_id=tid, root=root_final, session_id=row.get("session_id"))
        if before_final or after_final:
            entry["adjacent"] = get_adjacent_turns(
                turn_id=tid,
                root=root_final,
                session_id=row.get("session_id"),
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
