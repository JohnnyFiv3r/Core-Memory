from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from core_memory.persistence import events
from core_memory.persistence.io_utils import append_jsonl, store_lock
from core_memory.policy.hygiene import enforce_bead_hygiene_contract
from core_memory.retrieval.lifecycle import mark_semantic_dirty
from core_memory.runtime.session_surface import read_session_surface


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

    return bead_id


__all__ = ["add_bead_for_store"]
