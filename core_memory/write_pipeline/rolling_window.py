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

from core_memory.persistence.store import MemoryStore
from core_memory.persistence.rolling_record_store import write_rolling_records


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


def _load_filtered_beads(root: str) -> tuple[list[dict[str, Any]], set[str]]:
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
    return filtered, excluded_superseded


def _select_beads_for_budget(filtered: list[dict[str, Any]], *, token_budget: int, max_beads: int) -> tuple[list[dict[str, Any]], int]:
    included: list[dict[str, Any]] = []
    total = 0

    # Injection contract: always include the latest archived bead first.
    # This keeps the freshest turn visible for association opportunities.
    remaining = list(filtered)
    if remaining and max_beads > 0:
        latest = remaining.pop(0)
        included.append(latest)
        total += estimate_tokens(render_bead(latest))

    for bead in remaining:
        if len(included) >= max_beads:
            break
        chunk = render_bead(bead)
        t = estimate_tokens(chunk)
        if included and (total + t > token_budget):
            break
        if (not included) and (t > token_budget):
            break
        included.append(bead)
        total += t
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
        "selection_policy": "strict_recency_fifo_with_budget_forced_latest",
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
    filtered, excluded_superseded = _load_filtered_beads(root)
    included, total = _select_beads_for_budget(
        filtered,
        token_budget=int(token_budget),
        max_beads=int(max_beads),
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
