from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core_memory.store import MemoryStore


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


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


def build_rolling_surface(root: str, token_budget: int = 3000, max_beads: int = 80):
    memory = MemoryStore(root=root)
    idx = memory._read_json(memory.beads_dir / "index.json")
    beads_map = idx.get("beads") or {}
    beads = list(beads_map.values())

    excluded_superseded = set(str(x) for x in idx.get("superseded_ids", []))
    filtered = [b for b in beads if str(b.get("id") or "") not in excluded_superseded]

    filtered.sort(key=lambda b: str(b.get("promoted_at") or b.get("created_at") or ""), reverse=True)

    included = []
    total = 0
    for bead in filtered:
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

    excluded_ids = [str(b.get("id") or "") for b in filtered if b not in included]
    included_ids = [str(b.get("id") or "") for b in included]

    text = "\n".join(render_bead(b) for b in included)
    meta = {
        "selected": len(included),
        "available": len(filtered),
        "token_estimate": total,
        "token_budget": int(token_budget),
        "max_beads": int(max_beads),
        "excluded_superseded": len(excluded_superseded),
        "surface": "rolling_window",
        "selection_policy": "strict_recency_fifo_with_budget",
        "compression_scope": "rolling_only",
        "owner_module": "core_memory.rolling_surface",
    }
    return text, meta, included_ids, excluded_ids


def write_rolling_surface(workspace_root: str | Path, text: str, meta: dict | None = None, included_ids: list[str] | None = None, excluded_ids: list[str] | None = None) -> str:
    p = Path(workspace_root) / "promoted-context.md"
    p.write_text(text, encoding="utf-8")

    meta_path = Path(workspace_root) / "promoted-context.meta.json"
    payload = {
        "surface": "rolling_window",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "meta": dict(meta or {}),
        "included_bead_ids": [str(x) for x in (included_ids or [])],
        "excluded_bead_ids": [str(x) for x in (excluded_ids or [])],
    }
    meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(p)
