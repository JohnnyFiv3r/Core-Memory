from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import logging
from importlib import import_module
from pathlib import Path
from typing import Any

from core_memory.persistence import events
from core_memory.persistence.io_utils import append_jsonl, store_lock
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
MAINTENANCE_RESULTS_FILE = "maintenance-results.jsonl"


def _enqueue_side_effect_event_provider(**kwargs: Any) -> dict[str, Any]:
    queue_module = import_module("core_memory.runtime.queue.side_effect_queue")
    return queue_module.enqueue_side_effect_event(**kwargs)


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
    selector, _metadata = _split_source_selector(source)
    return selector


def _split_source_selector(source: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (matching selector, audit-only metadata).

    New callers should send ``{"selector": {...}, "metadata": {...}}``. For
    flat legacy payloads, only strong source identifiers participate in matching;
    everything else is preserved for the tombstone/audit trail.
    """
    src = dict(source or {})
    explicit_selector = src.get("selector") if isinstance(src.get("selector"), dict) else None
    explicit_metadata = src.get("metadata") if isinstance(src.get("metadata"), dict) else None
    raw_selector = dict(explicit_selector or src)
    selector = {
        str(k): v
        for k, v in raw_selector.items()
        if str(k) in STRONG_SOURCE_KEYS and _stable_ref(v)
    }

    metadata: dict[str, Any] = {}
    if explicit_metadata is not None:
        metadata.update({str(k): v for k, v in explicit_metadata.items() if _stable_ref(v)})
    if explicit_selector is None:
        for k, v in src.items():
            if str(k) in {"selector", "metadata"}:
                continue
            if str(k) not in STRONG_SOURCE_KEYS and _stable_ref(v):
                metadata[str(k)] = v
    else:
        for k, v in src.items():
            if str(k) in {"selector", "metadata"}:
                continue
            if _stable_ref(v):
                metadata.setdefault(str(k), v)
    return selector, metadata


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


def _events_dir(root: Any) -> Path:
    p = Path(root) / ".beads" / "events"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _maintenance_results_path(root: Any) -> Path:
    return _events_dir(root) / MAINTENANCE_RESULTS_FILE


def _result_fingerprint(action: str, payload: dict[str, Any]) -> str:
    body = json.dumps(
        {"action": str(action), "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _lookup_idempotency_result(root: Any, *, idempotency_key: str, fingerprint: str) -> dict[str, Any] | None:
    key = _clean_str(idempotency_key)
    if not key:
        return None
    path = _maintenance_results_path(root)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if _clean_str(row.get("idempotency_key")) != key:
                continue
            if _clean_str(row.get("fingerprint")) != fingerprint:
                return {
                    "ok": False,
                    "error": "idempotency_key_conflict",
                    "contract": "core_memory.maintenance_idempotency.v1",
                    "idempotency_key": key,
                }
            result = dict(row.get("result") or {})
            result["idempotent_replay"] = True
            result["idempotency_key"] = key
            return result
    return None


def _record_idempotency_result(root: Any, *, idempotency_key: str, fingerprint: str, result: dict[str, Any]) -> None:
    key = _clean_str(idempotency_key)
    if not key:
        return
    with store_lock(Path(root)):
        existing = _lookup_idempotency_result(root, idempotency_key=key, fingerprint=fingerprint)
        if existing is not None:
            return
        append_jsonl(
            _maintenance_results_path(root),
            {
                "contract": "core_memory.maintenance_idempotency.v1",
                "idempotency_key": key,
                "fingerprint": fingerprint,
                "created_at": _now(),
                "result": dict(result),
            },
        )


def _enqueue_myelination_update(root: Any, *, reason: str = "maintenance_graph_change", idempotency_key: str = "") -> dict[str, Any]:
    try:
        key = _clean_str(idempotency_key) or f"myelination:{reason}"
        return _enqueue_side_effect_event_provider(
            root=root,
            kind="myelination-update",
            payload={"reason": reason, "source": "maintain", "idempotency_key": key},
            idempotency_key=key,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _enqueue_bead_retraction_retry(
    root: Any,
    *,
    bead_ids: list[str],
    failures: list[dict[str, Any]],
    reason: str,
    idempotency_key: str = "",
) -> dict[str, Any]:
    if not failures:
        return {"ok": True, "queued": False, "failure_count": 0}
    try:
        ids = [_clean_str(x) for x in bead_ids if _clean_str(x)]
        key = _clean_str(idempotency_key) or "bead-retraction:" + hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()[:16]
        out = _enqueue_side_effect_event_provider(
            root=root,
            kind="bead-retraction",
            payload={
                "bead_ids": ids,
                "failures": failures,
                "reason": reason,
                "idempotency_key": key,
            },
            idempotency_key=key,
        )
        out["failure_count"] = len(failures)
        return out
    except Exception as exc:
        return {"ok": False, "queued": False, "failure_count": len(failures), "error": str(exc)}


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


def _mirror_removed_beads_to_graph(root: Any, bead_ids: list[str]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    if not bead_ids:
        return failures
    if os.environ.get("CORE_MEMORY_GRAPH_BACKEND", "kuzu").strip().lower() in ("none", ""):
        return failures
    log = logging.getLogger(__name__)
    try:
        from core_memory.persistence.graph.factory import create_graph_backend

        graph = create_graph_backend(Path(root))
        for bead_id in bead_ids:
            try:
                graph.on_bead_retracted(str(bead_id))
            except Exception as exc:
                log.warning("graph on_bead_retracted failed for %s: %s", bead_id, exc)
                failures.append({"target": "graph", "bead_id": str(bead_id), "error": str(exc)})
    except Exception as exc:
        log.warning("graph backend removal mirror failed: %s", exc)
        failures.append({"target": "graph", "bead_id": "", "error": str(exc)})
    return failures


def _create_sync_targets() -> list[Any]:
    targets_env = (os.environ.get("CORE_MEMORY_SYNC_TARGETS") or "").strip().lower()
    if not targets_env or targets_env == "none":
        return []
    targets: list[Any] = []
    log = logging.getLogger(__name__)
    for name in [t.strip() for t in targets_env.split(",") if t.strip()]:
        if name == "obsidian":
            try:
                from core_memory.integrations.obsidian import ObsidianSyncTarget

                targets.append(ObsidianSyncTarget.from_env())
            except Exception as exc:
                log.warning("obsidian sync target init failed: %s", exc)
    return targets


def _mirror_removed_beads_to_sync_targets(bead_ids: list[str]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    if not bead_ids:
        return failures
    log = logging.getLogger(__name__)
    for target in _create_sync_targets():
        target_name = getattr(target, "name", "?")
        retract = getattr(target, "on_bead_retracted", None)
        delete = getattr(target, "delete_bead", None)
        remove = getattr(target, "remove_bead", None)
        hook = retract if callable(retract) else delete if callable(delete) else remove if callable(remove) else None
        if hook is None:
            log.warning("sync target %s has no bead removal hook", target_name)
            continue
        for bead_id in bead_ids:
            try:
                hook(str(bead_id))
            except Exception as exc:
                log.warning("sync target %s bead removal failed for %s: %s", target_name, bead_id, exc)
                failures.append({"target": f"sync:{target_name}", "bead_id": str(bead_id), "error": str(exc)})
    return failures


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
    source_payload, source_metadata = _split_source_selector(source)
    idem_fingerprint = ""
    if apply_requested and _clean_str(idempotency_key):
        idem_fingerprint = _result_fingerprint(
            "remove_beads",
            {
                "bead_ids": ids,
                "reason": reason_s,
                "source": source_payload,
            },
        )
        replay = _lookup_idempotency_result(store.root, idempotency_key=idempotency_key, fingerprint=idem_fingerprint)
        if replay is not None:
            return replay
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
            "source": source_payload,
            "source_metadata": source_metadata,
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
                "source_metadata": source_metadata,
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
                source_metadata=source_metadata,
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
    projection_failures = _mirror_removed_beads_to_graph(store.root, found)
    projection_failures.extend(_mirror_removed_beads_to_sync_targets(found))
    retraction_retry = _enqueue_bead_retraction_retry(
        store.root,
        bead_ids=found,
        failures=projection_failures,
        reason=reason_s,
        idempotency_key=_clean_str(idempotency_key) and f"bead-retraction:{_clean_str(idempotency_key)}",
    )
    result = {
        **preview,
        "applied": True,
        "dry_run": False,
        "removed_count": len(found),
        "removed_bead_ids": found,
        "projection_failures": projection_failures,
        "projection_retry": retraction_retry,
    }
    if idem_fingerprint:
        _record_idempotency_result(store.root, idempotency_key=idempotency_key, fingerprint=idem_fingerprint, result=result)
    return result


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
    selector, source_metadata = _split_source_selector(source)
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

    limit_n = max(1, int(limit))
    idem_fingerprint = ""
    if apply_requested and _clean_str(idempotency_key):
        idem_fingerprint = _result_fingerprint(
            "remove_source",
            {
                "selector": selector,
                "reason": _clean_str(reason or "source removed"),
            },
        )
        replay = _lookup_idempotency_result(store.root, idempotency_key=idempotency_key, fingerprint=idem_fingerprint)
        if replay is not None:
            return replay
    index = store._read_json(store.beads_dir / "index.json")
    all_bead_ids = [
        bid for bid, bead in (index.get("beads") or {}).items()
        if isinstance(bead, dict) and _matches_source(bead, selector)
    ]
    all_bead_ids = sorted(all_bead_ids)
    apply_requested = bool(apply) and not bool(dry_run)
    bead_ids = all_bead_ids if apply_requested else all_bead_ids[:limit_n]
    truncated = len(all_bead_ids) > len(bead_ids)
    if not bead_ids:
        result = {
            "ok": True,
            "contract": "core_memory.remove_source.v1",
            "applied": bool(apply) and not bool(dry_run),
            "dry_run": bool(dry_run),
            "matched_count": 0,
            "matched_total": 0,
            "preview_count": 0,
            "limit": limit_n,
            "truncated": False,
            "remaining_count": 0,
            "removed_count": 0,
            "beads": [],
            "association_count": 0,
            "association_ids": [],
            "source": selector,
            "source_metadata": source_metadata,
        }
        if idem_fingerprint:
            _record_idempotency_result(store.root, idempotency_key=idempotency_key, fingerprint=idem_fingerprint, result=result)
        return result
    delegated_authority = dict(authority or {})
    delegated_allowed = list(delegated_authority.get("allowed_authority") or [])
    delegated_allowed.append("remove_bead")
    delegated_authority["allowed_authority"] = list(dict.fromkeys(str(x) for x in delegated_allowed if str(x).strip()))
    delegated_idempotency_key = (
        f"{_clean_str(idempotency_key)}:remove_beads"
        if apply_requested and _clean_str(idempotency_key)
        else _clean_str(idempotency_key)
    )
    out = remove_beads_for_store(
        store,
        bead_ids=bead_ids,
        reason=reason or "source removed",
        actor=actor,
        authority=delegated_authority,
        dry_run=dry_run,
        apply=apply,
        source={"selector": selector, "metadata": source_metadata},
        idempotency_key=delegated_idempotency_key,
    )
    out["contract"] = "core_memory.remove_source.v1"
    out["source"] = selector
    out["matched_total"] = len(all_bead_ids)
    out["matched_count"] = len(all_bead_ids)
    out["preview_count"] = len(bead_ids)
    out["limit"] = limit_n
    out["truncated"] = truncated
    out["remaining_count"] = max(0, len(all_bead_ids) - len(bead_ids))
    out["source_metadata"] = source_metadata
    if idem_fingerprint:
        _record_idempotency_result(store.root, idempotency_key=idempotency_key, fingerprint=idem_fingerprint, result=out)
    return out


def retry_bead_retraction(root: str | Path, *, bead_ids: list[str]) -> dict[str, Any]:
    ids = [_clean_str(x) for x in (bead_ids or []) if _clean_str(x)]
    graph_failures = _mirror_removed_beads_to_graph(root, ids)
    sync_failures = _mirror_removed_beads_to_sync_targets(ids)
    failures = graph_failures + sync_failures
    return {
        "ok": not failures,
        "contract": "core_memory.bead_retraction_retry.v1",
        "bead_ids": ids,
        "failure_count": len(failures),
        "failures": failures,
    }


def mark_outdated_memory_for_store(
    store: Any,
    *,
    bead_id: str,
    reason: str,
    actor: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    bid = _clean_str(bead_id)
    reason_s = _clean_str(reason)
    if not bid:
        return {"ok": False, "error": "mark_outdated_requires_bead_id", "contract": "core_memory.mark_outdated.v1"}
    if not reason_s:
        return {"ok": False, "error": "mark_outdated_requires_reason", "contract": "core_memory.mark_outdated.v1"}
    now = _now()
    with store_lock(store.root):
        index_path = store.beads_dir / "index.json"
        index = store._read_json(index_path)
        bead = (index.get("beads") or {}).get(bid)
        if not isinstance(bead, dict):
            return {"ok": False, "error": "bead_not_found", "contract": "core_memory.mark_outdated.v1", "bead_id": bid}
        bead.update({
            "status": "outdated",
            "maintenance_state": "outdated",
            "outdated_at": now,
            "outdated_reason": reason_s,
            "outdated_by": _clean_str(actor),
        })
        index["beads"][bid] = bead
        store._write_json(index_path, index)

        from core_memory.persistence.store_lifecycle_ops import _append_bead_snapshot

        _append_bead_snapshot(store, bead)
        events.append_event(
            root=store.root,
            session_id=_clean_str(bead.get("session_id")) or None,
            event_type="bead_marked_outdated",
            payload={"bead_id": bid, "reason": reason_s, "actor": _clean_str(actor), "marked_at": now},
            use_lock=False,
        )
        mark_semantic_dirty(store.root, reason="mark_outdated")
        mark_trace_dirty(store.root, reason="mark_outdated")

    myelination = _enqueue_myelination_update(
        store.root,
        reason="mark_outdated",
        idempotency_key=_clean_str(idempotency_key) and f"myelination:{_clean_str(idempotency_key)}",
    )
    return {
        "ok": True,
        "contract": "core_memory.mark_outdated.v1",
        "bead_id": bid,
        "status": "outdated",
        "myelination_refresh": myelination,
    }


def supersede_memory_for_store(
    store: Any,
    *,
    bead_id: str,
    successor_bead_id: str,
    reason: str = "",
    actor: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    bid = _clean_str(bead_id)
    successor = _clean_str(successor_bead_id)
    if not bid or not successor:
        return {"ok": False, "error": "supersede_memory_requires_bead_ids", "contract": "core_memory.supersede_memory.v1"}
    index = store._read_json(store.beads_dir / "index.json")
    beads = index.get("beads") or {}
    if bid not in beads or successor not in beads:
        return {
            "ok": False,
            "error": "bead_not_found",
            "contract": "core_memory.supersede_memory.v1",
            "bead_id": bid,
            "successor_bead_id": successor,
        }
    ok = bool(store.supersede(bid, successor))
    association_id = ""
    if ok:
        associations = store._read_json(store.beads_dir / "index.json").get("associations") or []
        exists = any(
            _clean_str(a.get("source_bead")) == successor
            and _clean_str(a.get("target_bead")) == bid
            and _clean_str(a.get("relationship")).lower() == "supersedes"
            for a in associations
            if isinstance(a, dict)
        )
        if not exists:
            association_id = store.link(successor, bid, "supersedes", _clean_str(reason) or "maintain supersession", confidence=0.95)
        events.append_event(
            root=store.root,
            session_id=None,
            event_type="memory_superseded_by_maintenance",
            payload={
                "bead_id": bid,
                "successor_bead_id": successor,
                "reason": _clean_str(reason),
                "actor": _clean_str(actor),
            },
        )
    myelination = _enqueue_myelination_update(
        store.root,
        reason="supersede_memory",
        idempotency_key=_clean_str(idempotency_key) and f"myelination:{_clean_str(idempotency_key)}",
    )
    return {
        "ok": ok,
        "contract": "core_memory.supersede_memory.v1",
        "bead_id": bid,
        "successor_bead_id": successor,
        "association_id": association_id,
        "myelination_refresh": myelination,
        "error": None if ok else "supersede_failed",
    }


def correct_memory_for_store(
    store: Any,
    *,
    bead_id: str,
    correction: str,
    actor: str = "",
    reason: str = "",
    title: str = "",
    archive_target: bool = False,
    idempotency_key: str = "",
) -> dict[str, Any]:
    bid = _clean_str(bead_id)
    correction_s = _clean_str(correction)
    if not bid:
        return {"ok": False, "error": "correct_memory_requires_bead_id", "contract": "core_memory.correct_memory.v1"}
    if not correction_s:
        return {"ok": False, "error": "correct_memory_requires_correction", "contract": "core_memory.correct_memory.v1"}
    index = store._read_json(store.beads_dir / "index.json")
    target = (index.get("beads") or {}).get(bid)
    if not isinstance(target, dict):
        return {"ok": False, "error": "bead_not_found", "contract": "core_memory.correct_memory.v1", "bead_id": bid}

    session_id = _clean_str(target.get("session_id")) or "maintenance"
    correction_id = store.add_bead(
        type="context",
        title=_clean_str(title) or f"Correction for {bid}",
        summary=[correction_s[:240]],
        detail=correction_s,
        session_id=session_id,
        source_turn_ids=[f"maintain:correction:{bid}"],
        tags=["memory_correction", "maintenance"],
        corrects_bead_id=bid,
        revision_type="correction",
        correction_reason=_clean_str(reason),
        correction_actor=_clean_str(actor),
        _association_coverage=False,
    )
    association_id = store.link(correction_id, bid, "invalidates", _clean_str(reason) or "memory correction", confidence=0.95)
    archived = False
    if bool(archive_target):
        archived = bool(store.reject(bid, approver=_clean_str(actor), reason=_clean_str(reason) or "corrected by maintenance"))
    events.append_event(
        root=store.root,
        session_id=session_id,
        event_type="memory_corrected",
        payload={
            "bead_id": bid,
            "correction_bead_id": correction_id,
            "association_id": association_id,
            "archive_target": bool(archive_target),
            "archived": archived,
            "actor": _clean_str(actor),
            "reason": _clean_str(reason),
        },
    )
    mark_semantic_dirty(store.root, reason="correct_memory")
    mark_trace_dirty(store.root, reason="correct_memory")
    myelination = _enqueue_myelination_update(
        store.root,
        reason="correct_memory",
        idempotency_key=_clean_str(idempotency_key) and f"myelination:{_clean_str(idempotency_key)}",
    )
    return {
        "ok": True,
        "contract": "core_memory.correct_memory.v1",
        "bead_id": bid,
        "correction_bead_id": correction_id,
        "association_id": association_id,
        "archived_target": archived,
        "myelination_refresh": myelination,
    }


def deactivate_association_for_store(
    store: Any,
    *,
    association_id: str = "",
    source_bead: str = "",
    target_bead: str = "",
    relationship: str = "",
    reason: str = "",
    actor: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    assoc_id = _clean_str(association_id)
    src = _clean_str(source_bead)
    tgt = _clean_str(target_bead)
    rel = _clean_str(relationship).lower()
    reason_s = _clean_str(reason)
    if not assoc_id and not (src and tgt and rel):
        return {"ok": False, "error": "deactivate_association_requires_id_or_edge", "contract": "core_memory.deactivate_association.v1"}
    if not reason_s:
        return {"ok": False, "error": "deactivate_association_requires_reason", "contract": "core_memory.deactivate_association.v1"}

    now = _now()
    retracted_snapshot: dict[str, Any] | None = None
    event_id = ""
    with store_lock(store.root):
        index_path = store.beads_dir / "index.json"
        index = store._read_json(index_path)
        associations = [a for a in (index.get("associations") or []) if isinstance(a, dict)]
        target_assoc = None
        for assoc in associations:
            if assoc_id and _clean_str(assoc.get("id")) == assoc_id:
                target_assoc = assoc
                break
            if not assoc_id and (
                _clean_str(assoc.get("source_bead")) == src
                and _clean_str(assoc.get("target_bead")) == tgt
                and _clean_str(assoc.get("relationship")).lower() == rel
            ):
                target_assoc = assoc
                break
        if target_assoc is None:
            retracted_known = {
                _clean_str(x)
                for x in (index.get("retracted_association_ids") or [])
                if _clean_str(x)
            }
            if assoc_id and assoc_id in retracted_known:
                return {
                    "ok": True,
                    "contract": "core_memory.deactivate_association.v1",
                    "association_id": assoc_id,
                    "already_retracted": True,
                }
            return {"ok": False, "error": "association_not_found", "contract": "core_memory.deactivate_association.v1"}

        retracted_snapshot = dict(target_assoc)
        assoc_id_final = _clean_str(retracted_snapshot.get("id")) or assoc_id
        retracted_snapshot.update({
            "status": "retracted",
            "retracted_at": now,
            "retracted_by": _clean_str(actor),
            "retraction_reason": reason_s,
        })
        index["associations"] = [a for a in associations if _clean_str(a.get("id")) != assoc_id_final]
        ids = {_clean_str(x) for x in (index.get("retracted_association_ids") or []) if _clean_str(x)}
        ids.add(assoc_id_final)
        index["retracted_association_ids"] = sorted(ids)
        retracted = index.setdefault("retracted_associations", {})
        retracted[assoc_id_final] = retracted_snapshot
        index.setdefault("stats", {})["total_associations"] = len(index.get("associations") or [])
        store._write_json(index_path, index)
        event_id = events.event_association_retracted(
            store.root,
            association_id=assoc_id_final,
            retracted_at=now,
            actor=_clean_str(actor),
            reason=reason_s,
            association_snapshot=retracted_snapshot,
            use_lock=False,
        )
        mark_trace_dirty(store.root, reason="association_retracted")

    myelination = _enqueue_myelination_update(
        store.root,
        reason="deactivate_association",
        idempotency_key=_clean_str(idempotency_key) and f"myelination:{_clean_str(idempotency_key)}",
    )
    return {
        "ok": True,
        "contract": "core_memory.deactivate_association.v1",
        "association_id": _clean_str((retracted_snapshot or {}).get("id") or assoc_id),
        "event_id": event_id,
        "retracted": True,
        "myelination_refresh": myelination,
    }


__all__ = [
    "correct_memory_for_store",
    "deactivate_association_for_store",
    "mark_outdated_memory_for_store",
    "remove_beads_for_store",
    "remove_source_beads_for_store",
    "retry_bead_retraction",
    "supersede_memory_for_store",
]
