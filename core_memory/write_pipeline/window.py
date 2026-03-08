from __future__ import annotations

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


def build_rolling_window(root: str, token_budget: int = 3000, max_beads: int = 80):
    memory = MemoryStore(root=root)
    idx = memory._read_json(memory.beads_dir / "index.json")
    beads_map = idx.get("beads") or {}
    beads = list(beads_map.values())

    excluded_superseded = set(str(x) for x in idx.get("superseded_ids", []))
    filtered = [b for b in beads if str(b.get("id") or "") not in excluded_superseded]

    def sort_key(b: dict):
        return str(b.get("promoted_at") or b.get("created_at") or "")

    filtered.sort(key=sort_key, reverse=True)

    included = []
    total = 0
    for bead in filtered:
        if len(included) >= max_beads:
            break
        chunk = render_bead(bead)
        t = estimate_tokens(chunk)
        if included and (total + t > token_budget):
            continue
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
    }
    return text, meta, included_ids, excluded_ids


def write_promoted_context(workspace_root: str | Path, text: str) -> str:
    p = Path(workspace_root) / "promoted-context.md"
    p.write_text(text, encoding="utf-8")
    return str(p)
