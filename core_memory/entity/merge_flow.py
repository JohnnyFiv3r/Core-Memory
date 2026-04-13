from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from core_memory.entity.registry import ensure_entity_registry_for_index, normalize_entity_alias
from core_memory.persistence.io_utils import store_lock


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_entity_merge_proposals_for_index(index: dict[str, Any]) -> None:
    proposals = index.get("entity_merge_proposals")
    if not isinstance(proposals, dict):
        index["entity_merge_proposals"] = {}


def _entity_similarity(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, list[str]]:
    label_a = normalize_entity_alias(str(a.get("normalized_label") or a.get("label") or ""))
    label_b = normalize_entity_alias(str(b.get("normalized_label") or b.get("label") or ""))
    aliases_a = {normalize_entity_alias(str(x)) for x in (a.get("aliases") or [])}
    aliases_b = {normalize_entity_alias(str(x)) for x in (b.get("aliases") or [])}
    aliases_a = {x for x in aliases_a if x}
    aliases_b = {x for x in aliases_b if x}

    reasons: list[str] = []

    overlap = aliases_a.intersection(aliases_b)
    overlap_score = min(1.0, len(overlap) / max(1.0, len(aliases_a.union(aliases_b)))) if (aliases_a or aliases_b) else 0.0
    if overlap:
        reasons.append("alias_overlap")

    seq = 0.0
    if label_a and label_b:
        seq = SequenceMatcher(None, label_a, label_b).ratio()
        if seq >= 0.90:
            reasons.append("label_similarity_high")

    containment = 0.0
    if label_a and label_b and (label_a in label_b or label_b in label_a):
        containment = 1.0
        reasons.append("label_containment")

    score = (0.45 * overlap_score) + (0.40 * seq) + (0.15 * containment)
    return (float(score), reasons)


def _proposal_id(left_id: str, right_id: str) -> str:
    a, b = sorted([str(left_id), str(right_id)])
    digest = hashlib.sha1(f"{a}:{b}".encode("utf-8")).hexdigest()[:12]
    return f"entity-merge-{digest}"


def suggest_entity_merge_proposals_for_index(
    index: dict[str, Any],
    *,
    min_score: float = 0.86,
    max_pairs: int = 40,
    source: str = "heuristic",
) -> dict[str, Any]:
    ensure_entity_registry_for_index(index)
    ensure_entity_merge_proposals_for_index(index)

    entities = {str(k): dict(v or {}) for k, v in (index.get("entities") or {}).items()}
    active_ids = [eid for eid, row in entities.items() if str(row.get("status") or "active") == "active"]
    active_ids.sort()

    proposals = index.get("entity_merge_proposals") or {}
    created = 0
    suggested: list[dict[str, Any]] = []

    pair_count = 0
    for i in range(len(active_ids)):
        for j in range(i + 1, len(active_ids)):
            if pair_count >= int(max_pairs):
                break
            pair_count += 1
            left = active_ids[i]
            right = active_ids[j]
            score, reasons = _entity_similarity(entities[left], entities[right])
            if score < float(min_score):
                continue

            pid = _proposal_id(left, right)
            existing = proposals.get(pid)
            if isinstance(existing, dict) and str(existing.get("status") or "").lower() == "pending":
                suggested.append(existing)
                continue

            payload = {
                "id": pid,
                "kind": "entity_merge",
                "left_entity_id": left,
                "right_entity_id": right,
                "score": round(float(score), 4),
                "reasons": sorted(set(reasons)),
                "status": "pending",
                "source": str(source or "heuristic"),
                "created_at": _now(),
                "updated_at": _now(),
            }
            proposals[pid] = payload
            suggested.append(payload)
            created += 1

    index["entity_merge_proposals"] = proposals
    return {
        "ok": True,
        "created": int(created),
        "pending": int(sum(1 for p in proposals.values() if str((p or {}).get("status") or "") == "pending")),
        "proposals": sorted(suggested, key=lambda r: (-float(r.get("score") or 0.0), str(r.get("id") or ""))),
    }


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        s = str(x or "")
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def apply_entity_merge_for_index(
    index: dict[str, Any],
    *,
    keep_entity_id: str,
    merge_entity_id: str,
    reviewer: str = "",
    notes: str = "",
) -> dict[str, Any]:
    ensure_entity_registry_for_index(index)
    entities = index.get("entities") or {}

    keep = dict(entities.get(keep_entity_id) or {})
    drop = dict(entities.get(merge_entity_id) or {})
    if not keep or not drop:
        return {"ok": False, "error": "entity_missing"}
    if keep_entity_id == merge_entity_id:
        return {"ok": False, "error": "same_entity"}

    aliases = _dedupe_keep_order(
        [normalize_entity_alias(str(x)) for x in (keep.get("aliases") or []) + (drop.get("aliases") or [])]
        + [normalize_entity_alias(str(keep.get("normalized_label") or "")), normalize_entity_alias(str(drop.get("normalized_label") or ""))]
    )

    keep["aliases"] = aliases
    keep["confidence"] = max(float(keep.get("confidence") or 0.0), float(drop.get("confidence") or 0.0))
    keep_prov = [r for r in (keep.get("provenance") or []) if isinstance(r, dict)]
    drop_prov = [r for r in (drop.get("provenance") or []) if isinstance(r, dict)]
    keep["provenance"] = (keep_prov + drop_prov + [{"kind": "merge", "source": "entity_merge_review", "reviewer": str(reviewer), "notes": str(notes), "ts": _now(), "merged_entity_id": merge_entity_id}])[-120:]
    keep["updated_at"] = _now()

    drop["status"] = "merged"
    drop["merged_into"] = keep_entity_id
    drop["updated_at"] = _now()

    entities[keep_entity_id] = keep
    entities[merge_entity_id] = drop
    index["entities"] = entities

    alias_map = index.get("entity_aliases") or {}
    for a in aliases:
        alias_map[a] = keep_entity_id
    # remap aliases previously pointing to merged entity
    for alias, eid in list(alias_map.items()):
        if str(eid) == merge_entity_id:
            alias_map[str(alias)] = keep_entity_id
    index["entity_aliases"] = alias_map

    beads = index.get("beads") or {}
    changed = 0
    for bead_id, row in beads.items():
        if not isinstance(row, dict):
            continue
        ids = [str(x) for x in (row.get("entity_ids") or [])]
        if not ids:
            continue
        new_ids = [keep_entity_id if x == merge_entity_id else x for x in ids]
        new_ids = _dedupe_keep_order(new_ids)
        if new_ids != ids:
            row["entity_ids"] = new_ids
            beads[bead_id] = row
            changed += 1
    index["beads"] = beads

    return {
        "ok": True,
        "keep_entity_id": keep_entity_id,
        "merged_entity_id": merge_entity_id,
        "beads_updated": int(changed),
    }


def decide_entity_merge_proposal_for_index(
    index: dict[str, Any],
    *,
    proposal_id: str,
    decision: str,
    reviewer: str = "",
    notes: str = "",
    apply: bool = True,
    keep_entity_id: str | None = None,
) -> dict[str, Any]:
    ensure_entity_merge_proposals_for_index(index)
    proposals = index.get("entity_merge_proposals") or {}
    pid = str(proposal_id or "").strip()
    if not pid or pid not in proposals:
        return {"ok": False, "error": "proposal_not_found"}

    p = dict(proposals.get(pid) or {})
    decision_n = str(decision or "").strip().lower()
    if decision_n not in {"accept", "reject"}:
        return {"ok": False, "error": "invalid_decision"}

    p["status"] = "accepted" if decision_n == "accept" else "rejected"
    p["reviewer"] = str(reviewer or "")
    p["review_notes"] = str(notes or "")
    p["reviewed_at"] = _now()
    p["updated_at"] = _now()

    applied: dict[str, Any] | None = None
    if decision_n == "accept" and bool(apply):
        left = str(p.get("left_entity_id") or "")
        right = str(p.get("right_entity_id") or "")
        target_keep = str(keep_entity_id or left or "")
        target_drop = right if target_keep == left else left
        applied = apply_entity_merge_for_index(
            index,
            keep_entity_id=target_keep,
            merge_entity_id=target_drop,
            reviewer=str(reviewer or ""),
            notes=str(notes or ""),
        )
        p["applied"] = bool(applied.get("ok"))
        p["applied_result"] = applied

    proposals[pid] = p
    index["entity_merge_proposals"] = proposals

    return {
        "ok": True,
        "proposal": p,
        "status": p.get("status"),
        "applied": applied,
    }


def list_entity_merge_proposals_for_index(
    index: dict[str, Any],
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    ensure_entity_merge_proposals_for_index(index)
    rows = [dict(v or {}) for v in (index.get("entity_merge_proposals") or {}).values() if isinstance(v, dict)]
    if status:
        status_n = str(status).strip().lower()
        rows = [r for r in rows if str(r.get("status") or "").strip().lower() == status_n]
    rows.sort(key=lambda r: (str(r.get("status") or ""), -float(r.get("score") or 0.0), str(r.get("id") or "")))
    return rows[: max(1, int(limit))]


def _index_path(root: str | Path) -> Path:
    return Path(root) / ".beads" / "index.json"


def _default_index_shell() -> dict[str, Any]:
    return {
        "beads": {},
        "associations": [],
        "entities": {},
        "entity_aliases": {},
        "stats": {
            "total_beads": 0,
            "total_associations": 0,
            "created_at": _now(),
        },
        "projection": {
            "mode": "session_first_projection_cache",
            "rebuilt_at": None,
        },
    }


def _normalize_index_shape(index: dict[str, Any]) -> dict[str, Any]:
    out = dict(index or {})
    if not isinstance(out.get("beads"), dict):
        out["beads"] = {}
    if not isinstance(out.get("associations"), list):
        out["associations"] = []
    if not isinstance(out.get("entities"), dict):
        out["entities"] = {}
    if not isinstance(out.get("entity_aliases"), dict):
        out["entity_aliases"] = {}

    stats = out.get("stats")
    if not isinstance(stats, dict):
        stats = {}
    stats.setdefault("total_beads", len(out.get("beads") or {}))
    stats.setdefault("total_associations", len(out.get("associations") or []))
    stats.setdefault("created_at", _now())
    out["stats"] = stats

    projection = out.get("projection")
    if not isinstance(projection, dict):
        projection = {}
    projection.setdefault("mode", "session_first_projection_cache")
    projection.setdefault("rebuilt_at", None)
    out["projection"] = projection
    return out


def _read_index(root: str | Path) -> dict[str, Any]:
    p = _index_path(root)
    if not p.exists():
        return _default_index_shell()
    try:
        idx = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(idx, dict):
            return _normalize_index_shape(idx)
        return _default_index_shell()
    except Exception:
        return _default_index_shell()


def _write_index(root: str | Path, index: dict[str, Any]) -> None:
    p = _index_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_normalize_index_shape(index), indent=2), encoding="utf-8")
    tmp.replace(p)


def suggest_entity_merge_proposals(root: str | Path, *, min_score: float = 0.86, max_pairs: int = 40, source: str = "heuristic") -> dict[str, Any]:
    with store_lock(Path(root)):
        idx = _read_index(root)
        out = suggest_entity_merge_proposals_for_index(idx, min_score=min_score, max_pairs=max_pairs, source=source)
        _write_index(root, idx)
        return out


def decide_entity_merge_proposal(
    root: str | Path,
    *,
    proposal_id: str,
    decision: str,
    reviewer: str = "",
    notes: str = "",
    apply: bool = True,
    keep_entity_id: str | None = None,
) -> dict[str, Any]:
    with store_lock(Path(root)):
        idx = _read_index(root)
        out = decide_entity_merge_proposal_for_index(
            idx,
            proposal_id=proposal_id,
            decision=decision,
            reviewer=reviewer,
            notes=notes,
            apply=apply,
            keep_entity_id=keep_entity_id,
        )
        _write_index(root, idx)
        return out


def list_entity_merge_proposals(root: str | Path, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    idx = _read_index(root)
    return list_entity_merge_proposals_for_index(idx, status=status, limit=limit)


def apply_entity_merge_direct(
    root: str | Path,
    *,
    keep_entity_id: str,
    merge_entity_id: str,
    reviewer: str = "",
    notes: str = "",
) -> dict[str, Any]:
    with store_lock(Path(root)):
        idx = _read_index(root)
        out = apply_entity_merge_for_index(
            idx,
            keep_entity_id=keep_entity_id,
            merge_entity_id=merge_entity_id,
            reviewer=reviewer,
            notes=notes,
        )
        _write_index(root, idx)
        return out
