from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core_memory.persistence import events
from core_memory.persistence.io_utils import atomic_write_json, store_lock
from core_memory.retrieval.lifecycle import mark_trace_dirty
from core_memory.schema.normalization import canonicalize_association_edge


def _load_index(root: Path) -> dict[str, Any]:
    path = root / ".beads" / "index.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    keys = ("source_bead", "target_bead", "relationship", "relationship_raw", "endpoints_swapped")
    return any(before.get(k) != after.get(k) for k in keys)


def _plan_relation_canonicalization(index: dict[str, Any], max_changes: int | None) -> dict[str, Any]:
    associations = list(index.get("associations") or [])
    changes: list[dict[str, Any]] = []
    rewritten: list[dict[str, Any]] = []
    skipped = 0
    matched_total = 0

    for assoc in associations:
        if not isinstance(assoc, dict):
            rewritten.append(assoc)
            continue
        before = dict(assoc)
        edge = canonicalize_association_edge(
            before.get("source_bead") or before.get("source_bead_id"),
            before.get("target_bead") or before.get("target_bead_id"),
            before.get("relationship") or before.get("rel"),
        )
        src = str(edge.get("source_bead") or "").strip()
        tgt = str(edge.get("target_bead") or "").strip()
        rel = str(edge.get("relationship") or "").strip()
        if not src or not tgt or not rel:
            skipped += 1
            rewritten.append(before)
            continue
        after = dict(before)
        after["source_bead"] = src
        after["target_bead"] = tgt
        after["relationship"] = rel
        if edge.get("normalization_applied") and not after.get("relationship_raw"):
            after["relationship_raw"] = str(edge.get("relationship_raw") or "")
        if edge.get("endpoints_swapped"):
            after["endpoints_swapped"] = True
        if _changed(before, after):
            matched_total += 1
            if max_changes is None or len(changes) < max_changes:
                changes.append({
                    "association_id": str(before.get("id") or ""),
                    "before": before,
                    "after": after,
                })
                rewritten.append(after)
            else:
                rewritten.append(before)
        else:
            rewritten.append(before)

    return {
        "changes": changes,
        "rewritten": rewritten,
        "matched_count": matched_total,
        "planned_count": len(changes),
        "skipped_count": skipped,
        "truncated": bool(max_changes is not None and matched_total > len(changes)),
    }


def canonicalize_associations_for_store(
    root: str | Path,
    *,
    apply: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Rewrite inverse-direction association labels into active canonical edges."""
    root_path = Path(root)
    index_path = root_path / ".beads" / "index.json"
    max_changes = max(0, int(limit)) if limit is not None else None

    plan = _plan_relation_canonicalization(_load_index(root_path), max_changes)
    changes = list(plan.get("changes") or [])

    out = {
        "ok": True,
        "apply": bool(apply),
        "contract": "core_memory.association_relation_canonicalization.v1",
        "matched_count": int(plan.get("matched_count") or 0),
        "planned_count": int(plan.get("planned_count") or 0),
        "skipped_count": int(plan.get("skipped_count") or 0),
        "truncated": bool(plan.get("truncated")),
        "sample": changes[:20],
    }
    if not apply:
        return out

    with store_lock(root_path):
        current = _load_index(root_path)
        plan = _plan_relation_canonicalization(current, max_changes)
        changes = list(plan.get("changes") or [])
        out.update({
            "matched_count": int(plan.get("matched_count") or 0),
            "planned_count": int(plan.get("planned_count") or 0),
            "skipped_count": int(plan.get("skipped_count") or 0),
            "truncated": bool(plan.get("truncated")),
            "sample": changes[:20],
        })
        if not changes:
            out["applied_count"] = 0
            return out
        current["associations"] = list(plan.get("rewritten") or [])
        current.setdefault("stats", {})["total_associations"] = len(current["associations"])
        index_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(index_path, current)
        for change in changes:
            after = dict(change.get("after") or {})
            assoc_id = str(after.get("id") or change.get("association_id") or "").strip()
            if assoc_id:
                events.event_association_canonicalized(
                    root_path,
                    association_id=assoc_id,
                    association_snapshot=after,
                    previous_association=dict(change.get("before") or {}),
                    use_lock=False,
                )
        mark_trace_dirty(root_path, reason="relation_canonicalization")

    out["applied_count"] = len(changes)
    return out
