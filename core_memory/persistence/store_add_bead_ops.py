from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from core_memory.persistence import events
from core_memory.entity.registry import sync_bead_entities_for_index
from core_memory.persistence.io_utils import append_jsonl, store_lock
from core_memory.policy.hygiene import enforce_bead_hygiene_contract, is_generic_title
from core_memory.retrieval.lifecycle import mark_semantic_dirty
from core_memory.runtime.session.session_surface import read_session_surface
from core_memory.schema.normalization import (
    CANONICAL_BEAD_TYPES,
    normalize_bead_type,
    resolve_confidence_class,
    resolve_grounding,
)


def _find_last_session_bead(index: dict, session_id: str) -> str | None:
    """Return the bead_id of the most-recently created bead in the session, or None."""
    candidates = [
        (str((b or {}).get("created_at") or ""), str(bid))
        for bid, b in (index.get("beads") or {}).items()
        if str((b or {}).get("session_id") or "") == str(session_id)
    ]
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


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

    # Known extra fields — all others are accepted but logged
    _KNOWN_EXTRA_FIELDS = {
        "confidence", "linked_bead_id", "result", "goal_id", "success_criteria",
        "condition", "action", "tested_by", "hypothesis_status", "reflection_type",
        "tool", "capability", "tool_result_status", "tool_output_id", "tool_output_ids",
        "supports_bead_ids", "blocked_by_description", "blocking_bead_id",
        "incident_id", "severity", "resolved_at", "constraints", "state_change",
        "observed_at", "recorded_at", "effective_from", "effective_to",
        "supersedes", "superseded_by", "claims", "claim_updates", "interaction_role",
        "memory_outcome", "context_tags", "revises_bead_id", "revision_type",
        "entity_ids", "evidence_refs", "speaker_attribution",
        "attributed_entity_id", "resolution_confidence", "prev_bead_id",
        "next_bead_id", "turn_index", "session_id", "promoted", "promotion_candidate",
        "promotion_locked", "promoted_at", "promotion_score", "promotion_threshold",
        "promotion_reason", "failure_signature", "decision_conflict_with",
        "unjustified_flip", "validation_warnings", "type_log", "type_coerced_from",
        "anchor_reason", "semantic_score", "retrieval_score",
        "data_type_flag", "source_id", "source_event_id", "source_system",
        "source_kind", "source_ref", "source_refs", "source_attribution",
        "core_memory_unifying_id", "hydration_ref", "transcript_id",
        "conversation_id", "source_thread_id", "source_session_id",
        "message_refs", "speaker_refs", "document_id", "raw_source_object_id",
        "ragie_document_id", "document_name", "mime_type", "document_kind",
        "document_date", "author_or_owner", "section_refs", "source_table",
        "source_record_id", "record_action", "record_grain",
        "business_object_type", "business_object_id", "metric_name",
        "metric_value", "metric_unit", "change_pct", "currency",
        "as_of_timestamp", "entity_refs", "attribute_tags",
        "derived_from", "derived_from_bead_ids", "assertion_kind",
        "assertion_subject", "assertion_predicate", "assertion_value",
        "confidence_class", "grounding", "actor",
        "approval_status", "approved_by", "approved_at", "approval_note",
    }

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
        "confidence_class": "C",
        "recall_count": 0,
        "last_recalled": None,
        **kwargs,
    }

    # Speaker attribution: promote key fields to bead top-level when present
    if isinstance(bead.get("speaker_attribution"), dict):
        attr = bead["speaker_attribution"]
        eid = str(attr.get("resolved_entity_id") or "").strip()
        conf = attr.get("resolution_confidence")
        if eid:
            bead.setdefault("attributed_entity_id", eid)
        if conf is not None:
            bead.setdefault("resolution_confidence", float(conf))

    # Grounding (how this is known) gates the C/B/A class; resolve both via the
    # shared resolvers so every write path computes them identically. Honors a
    # caller-provided grounding/confidence_class in kwargs (monotonic floor).
    bead["grounding"] = resolve_grounding(bead)
    bead["confidence_class"] = resolve_confidence_class(bead)

    now_iso = bead.get("created_at") or datetime.now(timezone.utc).isoformat()
    if not bead.get("type_log"):
        bead["type_log"] = [{"type": bead.get("type", ""), "set_at": now_iso, "reason": "initial_write"}]

    # Global rule: beads are records, eligible unless type is unrecognized.
    # normalize_bead_type resolves legacy aliases (e.g. promoted_lesson → lesson)
    # before the canonical membership check so legacy callers aren't penalized.
    bead["retrieval_eligible"] = (
        normalize_bead_type(str(bead.get("type") or "")) in CANONICAL_BEAD_TYPES
        and not is_generic_title(str(bead.get("title") or ""))
    )

    bead = store._sanitize_bead_content(bead)
    bead = enforce_bead_hygiene_contract(bead)

    if bead.get("type") in {"decision", "design_principle", "goal"} and not bead.get("constraints"):
        basis = " ".join([bead.get("title", "")] + list(bead.get("summary") or []))
        extracted = store.extract_constraints(basis)
        if extracted:
            bead["constraints"] = extracted

    if bead.get("type") == "hypothesis":
        basis = " ".join(bead.get("summary", [])) or bead.get("title", "") or bead.get("detail", "")
        bead["failure_signature"] = store.compute_failure_signature(basis)

    store._validate_bead_fields(bead)

    repeat_failure = False
    decision_conflicts = 0
    unjustified_flips = 0

    with store_lock(store.root):
        index_file = store.beads_dir / "index.json"
        index = store._read_json(index_file)
        index.setdefault("beads", {})
        index.setdefault("associations", [])
        index.setdefault("stats", {"total_beads": 0, "total_associations": 0})

        try:
            dedup_window = max(1, int(os.environ.get("CORE_MEMORY_WRITE_DEDUP_WINDOW", "25")))
        except ValueError:
            dedup_window = 25
        dup_id = store._find_recent_duplicate_bead_id(index, bead, session_id=resolved_session_id, window=dedup_window)
        if dup_id:
            return dup_id

        if resolved_session_id and not bead.get("prev_bead_id"):
            prev = _find_last_session_bead(index, resolved_session_id)
            if prev:
                bead["prev_bead_id"] = prev
                # Append next_bead_id to the previous bead in the same lock window
                if prev in index["beads"] and not index["beads"][prev].get("next_bead_id"):
                    index["beads"][prev]["next_bead_id"] = bead_id

        if resolved_session_id:
            bead_file = store.beads_dir / f"session-{resolved_session_id}.jsonl"
        else:
            bead_file = store.beads_dir / "global.jsonl"
        append_jsonl(bead_file, bead)

        if bead.get("type") == "hypothesis" and bead.get("failure_signature"):
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
    from core_memory.schema.bead_projection import build_retrieval_text
    return build_retrieval_text(bead)


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
            from core_memory.retrieval.semantic_index import (
                _create_external_backend, _qdrant_external_embeddings_enabled,
                _embed_vectors, _vector_rows, _vector_dim,
                _auto_configure_embedding_provider_from_keys, _default_embedding_model,
            )
            payload = _bead_payload(bead)
            text = _embed_text(bead)
            bead_id = str(bead.get("id") or "")
            if _qdrant_external_embeddings_enabled():
                # External provider (e.g. OpenAI 3072-dim): embed with the same provider
                # the batch build used so vector dimensions are compatible.
                provider = (_auto_configure_embedding_provider_from_keys() or "gemini").strip().lower()
                model = (os.environ.get("CORE_MEMORY_EMBEDDINGS_MODEL") or _default_embedding_model(provider)).strip()
                vecs = _embed_vectors(texts=[text], provider=provider, model=model, hash_dim=256)
                dim = _vector_dim(vecs, fallback=256)
                vec_backend = _create_external_backend(root=root_path, backend=VECTOR_BACKEND_QDRANT, dimension=dim)
                embs = _vector_rows(vecs)
                if embs:
                    vec_backend.upsert(bead_id=bead_id, embedding=embs[0], metadata=payload)
            else:
                # FastEmbed mode: dimension=0 lets QdrantBackend skip VectorParams creation;
                # upsert_texts uses client.add() which is FastEmbed-native.
                vec_backend = _create_external_backend(root=root_path, backend=VECTOR_BACKEND_QDRANT, dimension=0)
                vec_backend.upsert_texts(bead_ids=[bead_id], texts=[text], metadatas=[payload])
        except Exception as exc:
            _log.warning("qdrant upsert failed for bead %s: %s", bead.get("id"), exc)

    from core_memory.persistence.graph.factory import create_graph_backend
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
