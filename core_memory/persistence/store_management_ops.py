from __future__ import annotations

from datetime import datetime, timezone
import os
import logging
from pathlib import Path
from typing import Any

from core_memory.persistence import events
from core_memory.persistence.io_utils import store_lock
from core_memory.retrieval.lifecycle import mark_semantic_dirty, mark_trace_dirty


STRONG_SOURCE_KEYS = {
    "document_id",
    "ragie_document_id",
    "raw_source_object_id",
    "source_ref",
    "source_event_id",
    "source_record_id",
    "hydration_ref",
    "core_memory_unifying_id",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _stable_ref(value: Any) -> str:
    if isinstance(value, dict):
        items = [(str(k), value[k]) for k in sorted(value)]
        return "|".join(f"{k}={_stable_ref(v)}" for k, v in items)
    if isinstance(value, list):
        return "[" + ",".join(_stable_ref(v) for v in value) + "]"
    return _clean_str(value)


def _bead_summary(bead: dict[str, Any]) -> dict[str, Any]:
    return {
        "bead_id": _clean_str(bead.get("id")),
        "type": _clean_str(bead.get("type")),
        "title": _clean_str(bead.get("title")),
        "status": _clean_str(bead.get("status")),
        "session_id": _clean_str(bead.get("session_id")),
        "document_id": _clean_str(bead.get("document_id")),
        "source_id": _clean_str(bead.get("source_id")),
        "source_event_id": _clean_str(bead.get("source_event_id")),
        "source_ref": _clean_str(bead.get("source_ref")),
        "ragie_document_id": _clean_str(bead.get("ragie_document_id")),
        "raw_source_object_id": _clean_str(bead.get("raw_source_object_id")),
        "core_memory_unifying_id": _clean_str(bead.get("core_memory_unifying_id")),
    }


def _source_selector(source: dict[str, Any] | None) -> dict[str, Any]:
    return {str(k): v for k, v in dict(source or {}).items() if _stable_ref(v)}


def _validate_source_selector(selector: dict[str, Any]) -> str | None:
    if not selector:
        return "source_selector_required"
    if not any(k in selector for k in STRONG_SOURCE_KEYS):
        return "source_selector_requires_strong_identifier"
    return None


def _matches_source(bead: dict[str, Any], selector: dict[str, Any]) -> bool:
    for key, expected in selector.items():
        if key == "hydration_ref":
            if _stable_ref(bead.get("hydration_ref")) != _stable_ref(expected):
                return False
            continue
        if _stable_ref(bead.get(key)) != _stable_ref(expected):
            return False
    return True


def _authority_allows_remove(authority: dict[str, Any] | None, *, source_cleanup: bool = False) -> bool:
    auth = dict(authority or {})
    allowed = {str(x).strip() for x in (auth.get("allowed_authority") or []) if str(x).strip()}
    mode = _clean_str(auth.get("mode"))
    if bool(auth.get("user_confirmed")):
        return True
    if source_cleanup and mode == "event_hook":
        return True
    if "admin_repair" in allowed:
        return True
    if source_cleanup and "remove_source" in allowed:
        return True
    return "remove_bead" in allowed


def _remove_heads_for_beads(store: Any, removed_ids: set[str]) -> None:
    if not removed_ids:
        return
    heads = store._read_heads()
    changed = False
    for group in ("topics", "goals"):
        rows = heads.get(group)
        if not isinstance(rows, dict):
            continue
        for key, row in list(rows.items()):
            if _clean_str((row or {}).get("bead_id")) in removed_ids:
                rows.pop(key, None)
                changed = True
    if changed:
        store._write_heads(heads)


def _mirror_removed_beads_to_graph(root: Any, bead_ids: list[str]) -> None:
    if not bead_ids:
        return
    if os.environ.get("CORE_MEMORY_GRAPH_BACKEND", "kuzu").strip().lower() in ("none", ""):
        return
    log = logging.getLogger(__name__)
    try:
        from core_memory.persistence.graph.factory import create_graph_backend

        graph = create_graph_backend(Path(root))
        for bead_id in bead_ids:
            try:
                graph.on_bead_retracted(str(bead_id))
            except Exception as exc:
                log.warning("graph on_bead_retracted failed for %s: %s", bead_id, exc)
    except Exception as exc:
        log.warning("graph backend removal mirror failed: %s", exc)


def remove_beads_for_store(
    store: Any,
    *,
    bead_ids: list[str],
    reason: str = "",
    actor: str = "",
    authority: dict[str, Any] | None = None,
    dry_run: bool = True,
    apply: bool = False,
    source: dict[str, Any] | None = None,
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Remove beads from the active projection while preserving an audit tombstone.

    This is the destructive graph-management primitive. It does not rewrite
    archive JSONL files; instead it removes active index rows and appends a
    bead_removed event that rebuild_index() honors.
    """
    ids = [_clean_str(x) for x in (bead_ids or []) if _clean_str(x)]
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {"ok": False, "error": "remove_beads_requires_bead_ids", "contract": "core_memory.remove_beads.v1"}

    reason_s = _clean_str(reason)
    if not reason_s:
        return {"ok": False, "error": "remove_beads_requires_reason", "contract": "core_memory.remove_beads.v1"}

    apply_requested = bool(apply) and not bool(dry_run)
    if apply_requested and not _authority_allows_remove(authority):
        return {
            "ok": False,
            "error": "remove_beads_requires_confirmed_or_authorized_actor",
            "contract": "core_memory.remove_beads.v1",
        }

    now = _now()
    source_payload = _source_selector(source)
    with store_lock(store.root):
        index_path = store.beads_dir / "index.json"
        index = store._read_json(index_path)
        beads = index.setdefault("beads", {})
        associations = list(index.setdefault("associations", []))
        found = [bid for bid in ids if isinstance(beads.get(bid), dict)]
        missing = [bid for bid in ids if bid not in found]
        removed_set = set(found)
        removed_associations = [
            assoc for assoc in associations
            if _clean_str(assoc.get("source_bead")) in removed_set or _clean_str(assoc.get("target_bead")) in removed_set
        ]
        preview = {
            "ok": True,
            "contract": "core_memory.remove_beads.v1",
            "applied": False,
            "dry_run": True,
            "matched_count": len(found),
            "missing_bead_ids": missing,
            "beads": [_bead_summary(beads[bid]) for bid in found],
            "association_count": len(removed_associations),
            "association_ids": [_clean_str(a.get("id")) for a in removed_associations if _clean_str(a.get("id"))],
        }
        if not apply_requested:
            return preview

        removed_meta = index.setdefault("removed_beads", {})
        for bid in found:
            bead = dict(beads.pop(bid))
            session_id = _clean_str(bead.get("session_id")) or None
            association_ids = [
                _clean_str(a.get("id")) for a in removed_associations
                if _clean_str(a.get("id"))
                and (_clean_str(a.get("source_bead")) == bid or _clean_str(a.get("target_bead")) == bid)
            ]
            tombstone = {
                "bead_id": bid,
                "removed_at": now,
                "reason": reason_s,
                "actor": _clean_str(actor),
                "source": source_payload,
                "idempotency_key": _clean_str(idempotency_key),
                "removed_association_ids": association_ids,
                "bead": _bead_summary(bead),
            }
            removed_meta[bid] = tombstone
            events.event_bead_removed(
                store.root,
                session_id=session_id,
                bead_id=bid,
                removed_at=now,
                reason=reason_s,
                actor=_clean_str(actor),
                source=source_payload,
                idempotency_key=_clean_str(idempotency_key),
                removed_association_ids=association_ids,
                bead_snapshot=bead,
                use_lock=False,
            )

        index["associations"] = [
            assoc for assoc in associations
            if _clean_str(assoc.get("source_bead")) not in removed_set
            and _clean_str(assoc.get("target_bead")) not in removed_set
        ]
        index["removed_bead_ids"] = sorted(set(index.get("removed_bead_ids") or []).union(removed_set))
        index.setdefault("stats", {})
        index["stats"]["total_beads"] = len(index.get("beads") or {})
        index["stats"]["total_associations"] = len(index.get("associations") or [])
        index["projection"] = {"mode": "session_first_projection_cache", "rebuilt_at": now}
        store._write_json(index_path, index)
        _remove_heads_for_beads(store, removed_set)

    mark_semantic_dirty(store.root, reason="remove_bead")
    mark_trace_dirty(store.root, reason="remove_bead")
    _mirror_removed_beads_to_graph(store.root, found)
    return {
        **preview,
        "applied": True,
        "dry_run": False,
        "removed_count": len(found),
        "removed_bead_ids": found,
    }


def remove_source_beads_for_store(
    store: Any,
    *,
    source: dict[str, Any],
    reason: str = "",
    actor: str = "",
    authority: dict[str, Any] | None = None,
    dry_run: bool = True,
    apply: bool = False,
    idempotency_key: str = "",
    limit: int = 1000,
) -> dict[str, Any]:
    selector = _source_selector(source)
    invalid = _validate_source_selector(selector)
    if invalid:
        return {"ok": False, "error": invalid, "contract": "core_memory.remove_source.v1"}

    apply_requested = bool(apply) and not bool(dry_run)
    if apply_requested and not _authority_allows_remove(authority, source_cleanup=True):
        return {
            "ok": False,
            "error": "remove_source_requires_event_hook_or_authorized_actor",
            "contract": "core_memory.remove_source.v1",
        }

    index = store._read_json(store.beads_dir / "index.json")
    bead_ids = [
        bid for bid, bead in (index.get("beads") or {}).items()
        if isinstance(bead, dict) and _matches_source(bead, selector)
    ]
    bead_ids = sorted(bead_ids)[: max(1, int(limit))]
    if not bead_ids:
        return {
            "ok": True,
            "contract": "core_memory.remove_source.v1",
            "applied": bool(apply) and not bool(dry_run),
            "dry_run": bool(dry_run),
            "matched_count": 0,
            "removed_count": 0,
            "beads": [],
            "association_count": 0,
            "association_ids": [],
            "source": selector,
        }
    delegated_authority = dict(authority or {})
    delegated_allowed = list(delegated_authority.get("allowed_authority") or [])
    delegated_allowed.append("remove_bead")
    delegated_authority["allowed_authority"] = list(dict.fromkeys(str(x) for x in delegated_allowed if str(x).strip()))
    out = remove_beads_for_store(
        store,
        bead_ids=bead_ids,
        reason=reason or "source removed",
        actor=actor,
        authority=delegated_authority,
        dry_run=dry_run,
        apply=apply,
        source=selector,
        idempotency_key=idempotency_key,
    )
    out["contract"] = "core_memory.remove_source.v1"
    out["source"] = selector
    return out


__all__ = [
    "remove_beads_for_store",
    "remove_source_beads_for_store",
]
