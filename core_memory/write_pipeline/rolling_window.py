"""
Rolling surface renderer.

DEPRECATED: This module produces derived artifacts.
- rolling_record_store.py is the canonical rolling authority
- promoted-context.md and promoted-context.meta.json are operator-facing only

This module is kept for backward compatibility and as a renderer.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.persistence.rolling_record_store import write_rolling_records
from core_memory.persistence.store import MemoryStore
from core_memory.policy.promotion import (
    DIVERSITY_REQUIRED_TYPES,
    compute_selection_score,
)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def bead_to_record(bead: dict) -> dict:
    return {
        "id": bead.get("id"),
        "type": bead.get("type"),
        "status": bead.get("status"),
        "title": bead.get("title") or bead.get("snapshot_title"),
        "summary": list(bead.get("summary") or []),
        "created_at": bead.get("created_at") or bead.get("promoted_at"),
        "session_id": bead.get("session_id"),
    }


def render_bead(bead: dict) -> str:
    ts = bead.get("created_at") or bead.get("promoted_at") or ""
    bid = bead.get("id") or ""
    typ = bead.get("type") or "context"
    status = bead.get("status") or "open"
    title = bead.get("title") or bead.get("snapshot_title") or ""
    summary = bead.get("summary") or []
    if isinstance(summary, str):
        summary = [summary]
    summary_txt = " ".join([str(x).strip() for x in summary if str(x).strip()])
    return f"[{ts}] ({typ}/{status}) {title} #{bid}\n- {summary_txt}\n"


def _load_filtered_beads(root: str) -> tuple[list[dict[str, Any]], set[str], dict[str, Any]]:
    memory = MemoryStore(root=root)
    idx = memory._read_json(memory.beads_dir / "index.json")
    beads_map = idx.get("beads") or {}
    beads = list(beads_map.values())

    excluded_superseded = set(str(x) for x in idx.get("superseded_ids", []))
    # Retrieval/serving contract: rolling window includes promoted and archived beads,
    # excluding superseded beads. Open/candidate beads are also included so that
    # freshly created beads are visible for association and continuity.
    filtered = [
        b for b in beads
        if str(b.get("id") or "") not in excluded_superseded
        and str(b.get("status") or "").lower() not in ("superseded",)
    ]
    filtered.sort(key=lambda b: str((b.get("archive_ptr") or {}).get("revision_id") or b.get("created_at") or ""), reverse=True)
    return filtered, excluded_superseded, idx


def _is_lifecycle_bead(bead: dict[str, Any]) -> bool:
    """Check if a bead is a lifecycle/non-substantive type (for forced-latest filtering)."""
    btype = str(bead.get("type") or "").lower()
    if btype in {"session_start", "session_end", "checkpoint"}:
        return True
    if btype == "context" and not " ".join(bead.get("summary") or []).strip():
        return True
    return False


def _forced_latest_substantive(filtered: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find the latest substantive bead (skip lifecycle beads)."""
    for bead in filtered:
        if not _is_lifecycle_bead(bead):
            return bead
    return None


def _score_beads(filtered: list[dict[str, Any]], index: dict[str, Any]) -> list[tuple[dict[str, Any], float, dict]]:
    """Score all beads with selection_score and return sorted by score DESC."""
    scored = []
    for bead in filtered:
        sel_score, details = compute_selection_score(index, bead)
        scored.append((bead, sel_score, details))
    scored.sort(key=lambda x: (-x[1], str(x[0].get("id") or "")))
    return scored


def _ensure_type_diversity(
    included: list[dict[str, Any]],
    scored_remaining: list[tuple[dict[str, Any], float, dict]],
) -> list[dict[str, Any]]:
    """Guarantee at least one decision, lesson, and outcome if available."""
    included_ids = {str(b.get("id") or "") for b in included}
    included_types = {str(b.get("type") or "").lower() for b in included}
    missing_types = DIVERSITY_REQUIRED_TYPES - included_types
    if not missing_types:
        return included

    swaps: list[dict[str, Any]] = []
    for needed_type in sorted(missing_types):
        for bead, _score, _details in scored_remaining:
            bid = str(bead.get("id") or "")
            if bid in included_ids:
                continue
            if str(bead.get("type") or "").lower() == needed_type:
                swaps.append(bead)
                included_ids.add(bid)
                break

    if not swaps:
        return included

    # Replace the lowest-scored non-pinned beads from the tail
    result = list(included)
    swap_positions = list(range(len(result) - 1, 0, -1))  # tail to 1 (skip pin at 0)
    for i, swap_bead in enumerate(swaps):
        if i < len(swap_positions):
            result[swap_positions[i]] = swap_bead
        else:
            result.append(swap_bead)
    return result


def _select_beads_for_budget(
    filtered: list[dict[str, Any]],
    *,
    token_budget: int,
    max_beads: int,
    index: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    included: list[dict[str, Any]] = []
    total = 0

    # Pin the latest substantive bead first (skip lifecycle beads).
    pinned = _forced_latest_substantive(filtered)
    if pinned and max_beads > 0:
        included.append(pinned)
        total += estimate_tokens(render_bead(pinned))

    pinned_id = str(pinned.get("id") or "") if pinned else ""

    # Score remaining beads and fill by selection_score DESC.
    if index is not None:
        scored = _score_beads(filtered, index)
    else:
        # Fallback: use recency order (filtered is already sorted by recency)
        scored = [(b, 0.0, {}) for b in filtered]

    for bead, _score, _details in scored:
        if len(included) >= max_beads:
            break
        bid = str(bead.get("id") or "")
        if bid == pinned_id:
            continue
        chunk = render_bead(bead)
        t = estimate_tokens(chunk)
        if included and (total + t > token_budget):
            break
        if (not included) and (t > token_budget):
            break
        included.append(bead)
        total += t

    # Type diversity pass: ensure decision, lesson, outcome are represented.
    if index is not None and len(included) >= 2:
        included = _ensure_type_diversity(included, scored)
        total = sum(estimate_tokens(render_bead(b)) for b in included)

    return included, total


def _build_surface_payload(
    *,
    filtered: list[dict[str, Any]],
    included: list[dict[str, Any]],
    token_budget: int,
    max_beads: int,
    excluded_superseded_count: int,
    token_estimate: int,
) -> tuple[dict[str, Any], list[str], list[str]]:
    excluded_ids = [str(b.get("id") or "") for b in filtered if b not in included]
    included_ids = [str(b.get("id") or "") for b in included]

    records = [bead_to_record(b) for b in included]
    forced_latest_id = str(included[0].get("id") or "") if included else ""
    meta = {
        "selected": len(included),
        "available": len(filtered),
        "token_estimate": token_estimate,
        "token_budget": int(token_budget),
        "max_beads": int(max_beads),
        "excluded_superseded": int(excluded_superseded_count),
        "surface": "rolling_window",
        "selection_policy": "score_weighted_with_budget_forced_latest_substantive",
        "compression_scope": "rolling_only",
        "owner_module": "core_memory.write_pipeline.rolling_window",
        "rolling_record_store": "rolling-window.records.json",
        "record_count": len(records),
        "records": records,
        "forced_latest_bead_id": forced_latest_id,
    }
    return meta, included_ids, excluded_ids


def render_rolling_text(included: list[dict[str, Any]]) -> str:
    return "\n".join(render_bead(b) for b in included)


def build_rolling_surface(root: str, token_budget: int = 3000, max_beads: int = 80):
    filtered, excluded_superseded, idx = _load_filtered_beads(root)
    included, total = _select_beads_for_budget(
        filtered,
        token_budget=int(token_budget),
        max_beads=int(max_beads),
        index=idx,
    )
    text = render_rolling_text(included)
    meta, included_ids, excluded_ids = _build_surface_payload(
        filtered=filtered,
        included=included,
        token_budget=int(token_budget),
        max_beads=int(max_beads),
        excluded_superseded_count=len(excluded_superseded),
        token_estimate=total,
    )
    return text, meta, included_ids, excluded_ids


def _write_fallback_meta(
    workspace_root: str | Path,
    *,
    meta: dict,
    included_ids: list[str],
    excluded_ids: list[str],
) -> None:
    meta_path = Path(workspace_root) / "promoted-context.meta.json"
    payload = {
        "surface": "rolling_window",
        "role": "derived_fallback_metadata",
        "authority": "promoted_context_meta_fallback",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
        "included_bead_ids": [str(x) for x in (included_ids or [])],
        "excluded_bead_ids": [str(x) for x in (excluded_ids or [])],
    }
    meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_rolling_surface(
    workspace_root: str | Path,
    text: str,
    meta: dict | None = None,
    included_ids: list[str] | None = None,
    excluded_ids: list[str] | None = None,
) -> str:
    p = Path(workspace_root) / "promoted-context.md"
    p.write_text(text, encoding="utf-8")

    meta = dict(meta or {})
    meta.setdefault("authority", "rolling_record_store")
    meta.setdefault("derived_artifact", "promoted-context.md")
    records = list(meta.pop("records", []) or [])

    write_rolling_records(
        workspace_root,
        records=records,
        meta=meta,
        included_bead_ids=[str(x) for x in (included_ids or [])],
        excluded_bead_ids=[str(x) for x in (excluded_ids or [])],
    )
    _write_fallback_meta(
        workspace_root,
        meta=meta,
        included_ids=[str(x) for x in (included_ids or [])],
        excluded_ids=[str(x) for x in (excluded_ids or [])],
    )
    return str(p)
