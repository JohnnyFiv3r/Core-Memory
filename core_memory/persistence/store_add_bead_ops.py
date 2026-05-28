from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from core_memory.persistence import events
from core_memory.entity.registry import sync_bead_entities_for_index
from core_memory.persistence.io_utils import append_jsonl, store_lock
from core_memory.policy.hygiene import enforce_bead_hygiene_contract
from core_memory.retrieval.lifecycle import mark_semantic_dirty
from core_memory.runtime.session.session_surface import read_session_surface


def add_bead_for_store(
    store: Any,
    *,
    type: str,
    title: str,
    summary: Optional[list] = None,
    because: Optional[list] = None,
    source_turn_ids: Optional[list] = None,
    detail: str = "",
    session_id: Optional[str] = None,
    scope: str = "project",
    tags: Optional[list] = None,
    links: Optional[dict] = None,
    **kwargs,
) -> str:
    from core_memory.schema.models import BeadType, Scope

    type_value = store._normalize_enum(type, BeadType)
    scope_value = store._normalize_enum(scope, Scope)

    if type_value == "association":
        raise ValueError("association_is_not_a_bead_type")

    reserved_overrides = {"id"}
    bad_override = sorted(set(str(k) for k in kwargs.keys()).intersection(reserved_overrides))
    if bad_override:
        raise ValueError(f"reserved_overrides_not_allowed:{','.join(bad_override)}")

    bead_id = store._generate_id()
    now = datetime.now(timezone.utc).isoformat()

    resolved_session_id = store._resolve_bead_session_id(session_id=session_id, source_turn_ids=source_turn_ids)

    bead = {
        "id": bead_id,
        "type": type_value,
        "created_at": now,
        "session_id": resolved_session_id,
        "title": title,
        "summary": summary or [],
        "because": because or [],
        "source_turn_ids": source_turn_ids or [],
        "detail": detail,
        "scope": scope_value,
        "authority": "agent_inferred",
        "confidence": 0.8,
        "tags": tags or [],
        "links": store._normalize_links(links),
        "status": "open",
        "recall_count": 0,
        "last_recalled": None,
        **kwargs,
    }

    bead = store._sanitize_bead_content(bead)
    bead = enforce_bead_hygiene_contract(bead)

    if bead.get("type") in {"decision", "design_principle", "goal"} and not bead.get("constraints"):
        basis = " ".join([bead.get("title", "")] + list(bead.get("summary") or []))
        extracted = store.extract_constraints(basis)
        if extracted:
            bead["constraints"] = extracted

    if bead.get("type") == "failed_hypothesis":
        basis = " ".join(bead.get("summary", [])) or bead.get("title", "") or bead.get("detail", "")
        bead["failure_signature"] = store.compute_failure_signature(basis)

    store._validate_bead_fields(bead)

    repeat_failure = False
    decision_conflicts = 0
    unjustified_flips = 0

    with store_lock(store.root):
        index_file = store.beads_dir / "index.json"
        index = store._read_json(index_file)

        try:
            dedup_window = max(1, int(os.environ.get("CORE_MEMORY_WRITE_DEDUP_WINDOW", "25")))
        except ValueError:
            dedup_window = 25
        dup_id = store._find_recent_duplicate_bead_id(index, bead, session_id=resolved_session_id, window=dedup_window)
        if dup_id:
            return dup_id

        if resolved_session_id:
            bead_file = store.beads_dir / f"session-{resolved_session_id}.jsonl"
        else:
            bead_file = store.beads_dir / "global.jsonl"
        append_jsonl(bead_file, bead)

        if bead.get("type") == "failed_hypothesis" and bead.get("failure_signature"):
            sig = bead.get("failure_signature")
            repeat_failure = any(b.get("failure_signature") == sig for b in index.get("beads", {}).values())

        decision_conflicts, unjustified_flips, conflict_ids = store._detect_decision_conflicts(index, bead)
        if conflict_ids:
            bead["decision_conflict_with"] = conflict_ids
            bead["unjustified_flip"] = bool(unjustified_flips)

        # ER-1 canonical entity registry sync (bead-resident + index registry)
        sync_bead_entities_for_index(index, bead, source="add_bead")

        index["beads"][bead["id"]] = bead
        index["stats"]["total_beads"] = len(index["beads"])

        candidates = []
        if store.associate_on_add and store.assoc_top_k > 0:
            assoc_index = dict(index)
            assoc_beads = dict(index.get("beads") or {})
            if resolved_session_id:
                for row in read_session_surface(store.root, resolved_session_id):
                    rid = str((row or {}).get("id") or "")
                    if rid:
                        assoc_beads[rid] = row
            assoc_index["beads"] = assoc_beads
            candidates = store._quick_association_candidates(
                assoc_index,
                bead,
                max_lookback=store.assoc_lookback,
                top_k=store.assoc_top_k,
            )

        bead["association_preview"] = [
            {
                "bead_id": c["other_id"],
                "relationship": c["relationship"],
                "score": c["score"],
                "reason_code": c.get("reason_code"),
                "reason_text": c.get("reason_text"),
                "authoritative": False,
                "source": "store_quick_preview",
            }
            for c in candidates
        ]
        index["beads"][bead["id"]] = bead

        index["associations"] = sorted(
            index.get("associations", []),
            key=lambda a: (a.get("created_at", ""), a.get("id", "")),
        )
        index["stats"]["total_associations"] = len(index.get("associations", []))
        index["projection"] = {
            "mode": "session_first_projection_cache",
            "rebuilt_at": datetime.now(timezone.utc).isoformat(),
        }
        store._write_json(index_file, index)

        heads = store._read_heads()
        heads = store._update_heads_for_bead(heads, bead)
        store._write_heads(heads)

        events.event_bead_created(store.root, resolved_session_id, bead_id, now, use_lock=False)

        events.append_metric(
            store.root,
            {
                "ts": now,
                "run_id": f"bead-{bead_id}",
                "mode": "core_memory",
                "task_id": bead.get("type", "unknown"),
                "result": "success",
                "steps": 1,
                "tool_calls": 0,
                "beads_created": 1,
                "beads_recalled": 0,
                "repeat_failure": repeat_failure,
                "decision_conflicts": decision_conflicts,
                "unjustified_flips": unjustified_flips,
                "rationale_recall_score": 0,
                "turns_processed": 1,
                "compression_ratio": 1.0,
                "phase": "core_memory",
            },
            use_lock=False,
        )

    store.track_bead_created(1)
    mark_semantic_dirty(store.root, reason="add_bead")

    _mirror_bead_to_backends(store.root, bead)

    return bead_id


def _embed_text(bead: dict) -> str:
    title = str(bead.get("title") or "")
    summary = " ".join(str(s) for s in (bead.get("summary") or []))
    facts = " ".join(str(f) for f in (bead.get("retrieval_facts") or []))
    return f"{title}. {summary}. {facts}".strip(". ")


def _bead_payload(bead: dict) -> dict:
    return {
        "bead_id": str(bead.get("id") or ""),
        "type": str(bead.get("type") or ""),
        "session_id": str(bead.get("session_id") or ""),
        "created_at": str(bead.get("created_at") or ""),
        "retrieval_eligible": bool(bead.get("retrieval_eligible", True)),
        "status": str(bead.get("status") or "open"),
        "topics": [str(t) for t in (bead.get("tags") or [])],
        "entities": [str(e) for e in (bead.get("entities") or [])],
        "title": str(bead.get("title") or ""),
        "promoted": bool(bead.get("promotion_state") == "promoted"),
    }


def _mirror_bead_to_backends(root: Any, bead: dict) -> None:
    """Best-effort mirror to Qdrant and Kuzu. Failures log warnings, never raise."""
    import logging
    _log = logging.getLogger(__name__)
    from pathlib import Path
    root_path = Path(root)

    from core_memory.retrieval.semantic_index import _configured_vector_backend, VECTOR_BACKEND_QDRANT
    if _configured_vector_backend() == VECTOR_BACKEND_QDRANT and bead.get("retrieval_eligible", True):
        try:
            from core_memory.retrieval.semantic_index import _create_external_backend, _paths
            import json
            manifest_file, *_ = _paths(root_path)
            dimension = 1536
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                dimension = int(manifest.get("dimension") or 1536)
            except Exception:
                pass
            vec_backend = _create_external_backend(root=root_path, backend=VECTOR_BACKEND_QDRANT, dimension=dimension)
            payload = _bead_payload(bead)
            text = _embed_text(bead)
            bead_id = str(bead.get("id") or "")
            vec_backend.upsert_texts(bead_ids=[bead_id], texts=[text], metadatas=[payload])
        except Exception as exc:
            _log.warning("qdrant upsert failed for bead %s: %s", bead.get("id"), exc)

    from core_memory.persistence.graph.factory import create_graph_backend
    import os
    if os.environ.get("CORE_MEMORY_GRAPH_BACKEND", "kuzu").strip().lower() not in ("none", ""):
        try:
            graph = create_graph_backend(root_path)
            graph.on_bead_written(bead)
        except Exception as exc:
            _log.warning("graph on_bead_written failed for bead %s: %s", bead.get("id"), exc)

    for st in _create_sync_targets():
        try:
            st.on_bead_written(bead)
        except Exception as exc:
            _log.warning("sync target %s on_bead_written failed: %s", getattr(st, "name", "?"), exc)


def _create_sync_targets() -> list:
    """Instantiate configured sync targets from CORE_MEMORY_SYNC_TARGETS env var."""
    targets_env = (os.environ.get("CORE_MEMORY_SYNC_TARGETS") or "").strip().lower()
    if not targets_env or targets_env == "none":
        return []
    targets = []
    for name in [t.strip() for t in targets_env.split(",") if t.strip()]:
        if name == "obsidian":
            try:
                from core_memory.integrations.obsidian import ObsidianSyncTarget
                targets.append(ObsidianSyncTarget.from_env())
            except Exception as exc:
                _log.warning("obsidian sync target init failed: %s", exc)
    return targets


__all__ = ["add_bead_for_store"]
