from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from core_memory.claim.resolver import resolve_all_current_state
from core_memory.entity.merge_flow import list_entity_merge_proposals
from core_memory.entity.registry import load_entity_registry
from core_memory.runtime.ingress import maybe_emit_finalize_memory_event
from core_memory.runtime.jobs import async_jobs_status
from core_memory.runtime.turn_archive import find_turn_record, get_turn_tools as _get_turn_tools, get_adjacent_turns as _get_adjacent_turns
from core_memory.retrieval.semantic_index import semantic_doctor
from core_memory.write_pipeline.continuity_injection import load_continuity_injection
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
    adapter_kind: Optional[str] = None
    adapter_status: Optional[str] = None

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


def _safe_load_index(root_path: Path) -> dict[str, Any]:
    idx_path = root_path / ".beads" / "index.json"
    if not idx_path.exists():
        return {}
    try:
        payload = json.loads(idx_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _queue_breakdown(status_payload: dict[str, Any]) -> list[dict[str, Any]]:
    queues = dict((status_payload or {}).get("queues") or {}) if isinstance(status_payload, dict) else {}
    out: list[dict[str, Any]] = []
    for name, raw in sorted(queues.items(), key=lambda kv: str(kv[0])):
        row = dict(raw or {})
        out.append(
            {
                "kind": str(name),
                "ok": bool(row.get("ok", True)),
                "pending": int(row.get("pending") or row.get("queue_depth") or 0),
                "processable_now": int(row.get("processable_now") or row.get("ready") or 0),
                "retry_ready": int(row.get("retry_ready") or 0),
                "next_retry_at": row.get("next_retry_at"),
                "circuit_open": bool(row.get("circuit_open", False)),
                "last_error": str(row.get("last_error") or ""),
                "by_kind": dict(row.get("by_kind") or {}),
            }
        )
    return out


def _semantic_backend_summary(root_path: Path) -> dict[str, Any]:
    raw = semantic_doctor(root_path)
    r = dict(raw or {})
    return {
        "backend": str(r.get("backend") or "unknown"),
        "provider": str(r.get("provider") or "unknown"),
        "deployment_profile": str(r.get("deployment_profile") or "unknown"),
        "mode": str(r.get("mode") or "degraded_allowed"),
        "usable_backend": bool(r.get("usable_backend", False)),
        "multi_worker_safe": bool(r.get("multi_worker_safe", False)),
        "connectivity_checked": bool(r.get("connectivity_checked", False)),
        "connectivity_ok": bool(r.get("connectivity_ok", False)),
        "connectivity_error": str(r.get("connectivity_error") or ""),
        "concurrency_warning": str(r.get("concurrency_warning") or ""),
        "rows_count": int(r.get("rows_count") or 0),
        "next_step": str(r.get("next_step") or ""),
    }


def _recent_flushes_from_beads(beads_map: dict[str, Any], *, limit: int = 20) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for b in beads_map.values():
        row = dict(b or {})
        typ = str(row.get("type") or "")
        if typ not in {"process_flush", "session_start"}:
            continue
        rows.append(
            {
                "id": str(row.get("id") or ""),
                "type": typ,
                "title": str(row.get("title") or ""),
                "status": str(row.get("status") or ""),
                "session_id": str(row.get("session_id") or ""),
                "created_at": str(row.get("created_at") or ""),
            }
        )
    rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return rows[: max(1, int(limit))]


def inspect_state(
    *,
    root: Optional[str] = None,
    session_id: Optional[str] = None,
    as_of: Optional[str] = None,
    limit_beads: int = 200,
    limit_associations: int = 200,
    limit_flushes: int = 20,
    limit_merge_proposals: int = 40,
) -> dict[str, Any]:
    """Canonical inspect/observability read model.

    This is the stable public surface for dashboards/inspectors/demos.
    """
    root_final = _resolve_root(root)
    root_path = Path(root_final)
    index = _safe_load_index(root_path)
    beads_map = dict(index.get("beads") or {})

    beads_rows: list[dict[str, Any]] = []
    for b in sorted(beads_map.values(), key=lambda x: str((x or {}).get("created_at") or ""), reverse=True)[: max(1, int(limit_beads))]:
        row = dict(b or {})
        beads_rows.append(
            {
                "id": str(row.get("id") or ""),
                "type": str(row.get("type") or ""),
                "title": str(row.get("title") or ""),
                "summary": list(row.get("summary") or []),
                "status": str(row.get("status") or "open"),
                "session_id": str(row.get("session_id") or ""),
                "source_turn_ids": list(row.get("source_turn_ids") or []),
                "created_at": str(row.get("created_at") or ""),
                "interaction_role": str(row.get("interaction_role") or ""),
                "memory_outcome": str(row.get("memory_outcome") or ""),
                "claims_count": int(len(list(row.get("claims") or []))),
                "claim_updates_count": int(len(list(row.get("claim_updates") or []))),
                "hydrate_available": bool(list(row.get("source_turn_ids") or [])),
            }
        )

    associations_rows: list[dict[str, Any]] = []
    for i, a in enumerate(list(index.get("associations") or [])[: max(1, int(limit_associations))]):
        row = dict(a or {})
        associations_rows.append(
            {
                "id": str(row.get("id") or f"assoc-{i+1}"),
                "source_bead": str(row.get("source_bead") or ""),
                "target_bead": str(row.get("target_bead") or ""),
                "relationship": str(row.get("relationship") or ""),
                "explanation": str(row.get("explanation") or ""),
                "confidence": row.get("confidence", 0),
            }
        )

    rolling_rows: list[dict[str, Any]] = []
    try:
        ctx = load_continuity_injection(root_final)
        rolling_rows = [{"title": str(r.get("title") or ""), "type": str(r.get("type") or "")} for r in list((ctx or {}).get("records") or [])]
    except Exception:
        rolling_rows = []

    slots_out: list[dict[str, Any]] = []
    counts = {"active": 0, "conflict": 0, "retracted": 0, "historical": 0, "other": 0}
    try:
        state = resolve_all_current_state(root_final, as_of=(str(as_of).strip() or None))
        for slot_key, row in sorted((state.get("slots") or {}).items(), key=lambda kv: str(kv[0])):
            rr = dict(row or {})
            cur = dict(rr.get("current_claim") or {})
            status = str(rr.get("status") or "not_found")
            slots_out.append(
                {
                    "slot_key": str(slot_key),
                    "status": status,
                    "value": cur.get("value"),
                    "confidence": cur.get("confidence"),
                    "claim_id": cur.get("id"),
                    "conflict_count": int(len(list(rr.get("conflicts") or []))),
                    "history_count": int(len(list(rr.get("history") or []))),
                    "timeline_count": int(len(list(rr.get("timeline") or []))),
                }
            )
            if status in counts:
                counts[status] += 1
            elif status not in {"", "not_found"}:
                counts["other"] += 1
    except Exception:
        slots_out = []

    entity_rows: list[dict[str, Any]] = []
    entity_counts = {"total": 0, "active": 0, "merged": 0, "other": 0}
    merge_proposals: list[dict[str, Any]] = []
    try:
        reg = load_entity_registry(root_final)
        entities_map = dict((reg or {}).get("entities") or {})
        for entity_id, row in sorted(entities_map.items(), key=lambda kv: str((kv[1] or {}).get("updated_at") or ""), reverse=True):
            rr = dict(row or {})
            status = str(rr.get("status") or "active")
            entity_rows.append(
                {
                    "id": str(entity_id),
                    "label": str(rr.get("label") or ""),
                    "status": status,
                    "merged_into": str(rr.get("merged_into") or ""),
                    "aliases_count": int(len(list(rr.get("aliases") or []))),
                    "aliases": list(rr.get("aliases") or []),
                    "confidence": rr.get("confidence"),
                    "provenance_count": int(len(list(rr.get("provenance") or []))),
                    "updated_at": str(rr.get("updated_at") or ""),
                }
            )
            if status == "active":
                entity_counts["active"] += 1
            elif status == "merged":
                entity_counts["merged"] += 1
            else:
                entity_counts["other"] += 1
        entity_counts["total"] = int(len(entity_rows))
        merge_proposals = list_entity_merge_proposals(root_final, limit=max(1, int(limit_merge_proposals)))
    except Exception:
        entity_rows = []
        merge_proposals = []

    queue_status = async_jobs_status(root=root_final)
    return {
        "ok": True,
        "session": {
            "session_id": str(session_id or ""),
            "root": root_final,
        },
        "memory": {
            "beads": beads_rows,
            "associations": associations_rows,
            "rolling_window": rolling_rows,
        },
        "claims": {
            "slots": slots_out,
            "counts": counts,
            "as_of": (str(as_of).strip() or None),
        },
        "entities": {
            "rows": entity_rows,
            "counts": entity_counts,
            "merge_proposals": list(merge_proposals or [])[: max(1, int(limit_merge_proposals))],
        },
        "runtime": {
            "queue": queue_status,
            "queue_breakdown": _queue_breakdown(queue_status if isinstance(queue_status, dict) else {}),
            "semantic_backend": _semantic_backend_summary(root_path),
            "recent_flushes": _recent_flushes_from_beads(beads_map, limit=limit_flushes),
        },
        "stats": {
            "total_beads": int(len(beads_rows)),
            "total_associations": int(len(associations_rows)),
            "rolling_window_size": int(len(rolling_rows)),
            "claim_slot_count": int(len(slots_out)),
            "entity_count": int(len(entity_rows)),
        },
    }


def inspect_bead(*, root: Optional[str] = None, bead_id: str) -> dict[str, Any] | None:
    root_path = Path(_resolve_root(root))
    idx = _safe_load_index(root_path)
    beads = dict(idx.get("beads") or {})
    hit = beads.get(str(bead_id).strip())
    if not isinstance(hit, dict):
        return None
    return dict(hit)


def inspect_bead_hydration(
    *,
    root: Optional[str] = None,
    bead_id: str,
    include_tools: bool = False,
    before: int = 0,
    after: int = 0,
) -> dict[str, Any]:
    out = hydrate_bead_sources(
        root=root,
        bead_ids=[str(bead_id).strip()],
        include_tools=bool(include_tools),
        before=int(before),
        after=int(after),
    )
    return {"ok": True, **dict(out or {})}


def inspect_claim_slot(
    *,
    root: Optional[str] = None,
    subject: str,
    slot: str,
    as_of: Optional[str] = None,
) -> dict[str, Any]:
    root_final = _resolve_root(root)
    key = f"{str(subject).strip()}:{str(slot).strip()}"
    state = resolve_all_current_state(root_final, as_of=(str(as_of).strip() or None))
    row = dict((state.get("slots") or {}).get(key) or {})
    return {
        "ok": True,
        "slot_key": key,
        "as_of": (str(as_of).strip() or None),
        "row": row,
    }


def list_turn_summaries(
    *,
    root: Optional[str] = None,
    session_id: Optional[str] = None,
    limit: int = 200,
    cursor: Optional[str] = None,
) -> dict[str, Any]:
    root_path = Path(_resolve_root(root))
    turns_dir = root_path / ".turns"
    if not turns_dir.exists():
        return {"ok": True, "items": [], "cursor": None, "next_cursor": None, "total": 0}

    files: list[Path]
    sid = str(session_id or "").strip()
    if sid:
        files = [turns_dir / f"session-{sid}.jsonl"]
    else:
        files = sorted(turns_dir.glob("session-*.jsonl"))

    rows: list[dict[str, Any]] = []
    for p in files:
        if not p.exists():
            continue
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                raw = str(line or "").strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                rows.append(
                    {
                        "session_id": str(obj.get("session_id") or ""),
                        "turn_id": str(obj.get("turn_id") or ""),
                        "ts": str(obj.get("ts") or ""),
                        "origin": str(obj.get("origin") or ""),
                        "user_query": str(obj.get("user_query") or ""),
                        "assistant_final": str(obj.get("assistant_final") or ""),
                    }
                )
        except Exception:
            continue

    rows.sort(key=lambda x: str(x.get("ts") or ""), reverse=True)
    start = max(0, int(str(cursor or "0") or "0"))
    lim = max(1, int(limit or 200))
    items = rows[start : start + lim]
    next_cursor = str(start + lim) if (start + lim) < len(rows) else None
    return {
        "ok": True,
        "items": items,
        "cursor": str(start),
        "next_cursor": next_cursor,
        "total": int(len(rows)),
    }
