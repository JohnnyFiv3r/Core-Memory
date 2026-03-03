#!/usr/bin/env python3
"""Core Memory consolidation utility.

Usage:
  python3 consolidate.py consolidate --session <id> [--promote] [--token-budget 2000]
  python3 consolidate.py rolling-window [--token-budget 2000] [--max-beads 200]
"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from core_memory.store import MemoryStore


def workspace_root() -> Path:
    return Path(os.environ.get("OPENCLAW_WORKSPACE", "/home/node/.openclaw/workspace"))


def store_root() -> str:
    return os.environ.get("CORE_MEMORY_ROOT") or os.environ.get("MEMBEADS_ROOT") or str(workspace_root() / "memory")


def estimate_tokens(text: str) -> int:
    # Lightweight estimate: ~4 chars/token for English-like text.
    return max(1, (len(text) + 3) // 4)


def bead_render(bead: dict) -> str:
    lines = [f"- [{bead.get('type', 'context')}] {bead.get('title', '(untitled)')}"]
    for s in bead.get("summary", [])[:3]:
        lines.append(f"  - {s}")
    return "\n".join(lines)


def bead_sort_key(bead: dict):
    return bead.get("promoted_at") or bead.get("created_at") or ""


def render_rolling_window(store: MemoryStore, token_budget: int = 2000, max_beads: int = 200) -> tuple[str, dict]:
    # Pull a broad set then enforce rolling budget ourselves.
    promoted = store.query(status="promoted", limit=max_beads)
    promoted = sorted(promoted, key=bead_sort_key, reverse=True)

    lines = ["# Rolling Window (Core Memory)", ""]
    used = estimate_tokens("\n".join(lines))
    included = 0

    for bead in promoted:
        block = bead_render(bead)
        cost = estimate_tokens(block + "\n")
        if used + cost > token_budget:
            continue
        lines.append(block)
        used += cost
        included += 1

    if included == 0:
        lines.append("- (no promoted beads fit current token budget)")

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "token_budget": token_budget,
        "estimated_tokens_used": used,
        "candidate_promoted": len(promoted),
        "included": included,
        "excluded": max(0, len(promoted) - included),
    }
    lines.append("")
    lines.append(f"_meta: {json.dumps(meta, ensure_ascii=False)}_")

    return "\n".join(lines) + "\n", meta


def cmd_consolidate(args):
    store = MemoryStore(root=store_root())
    result = store.compact(session_id=args.session, promote=args.promote)

    rw, meta = render_rolling_window(store, token_budget=args.token_budget, max_beads=args.max_beads)
    out = workspace_root() / "promoted-context.md"
    out.write_text(rw)

    print(json.dumps({"ok": True, "consolidate": result, "rolling_window": str(out), "window_meta": meta}))


def cmd_rolling_window(args):
    store = MemoryStore(root=store_root())
    rw, meta = render_rolling_window(store, token_budget=args.token_budget, max_beads=args.max_beads)
    out = workspace_root() / "promoted-context.md"
    out.write_text(rw)
    print(json.dumps({"ok": True, "rolling_window": str(out), "window_meta": meta}))


def main():
    parser = argparse.ArgumentParser(prog="core-memory-consolidate")
    sub = parser.add_subparsers(dest="command", required=True)

    p1 = sub.add_parser("consolidate", help="Compact one session and refresh rolling window")
    p1.add_argument("--session", required=True)
    p1.add_argument("--promote", action="store_true")
    p1.add_argument("--token-budget", type=int, default=int(os.environ.get("CORE_MEMORY_WINDOW_TOKENS", "2000")))
    p1.add_argument("--max-beads", type=int, default=200)

    p2 = sub.add_parser("rolling-window", help="Refresh rolling window only")
    p2.add_argument("--token-budget", type=int, default=int(os.environ.get("CORE_MEMORY_WINDOW_TOKENS", "2000")))
    p2.add_argument("--max-beads", type=int, default=200)

    args = parser.parse_args()
    if args.command == "consolidate":
        cmd_consolidate(args)
    else:
        cmd_rolling_window(args)


if __name__ == "__main__":
    main()
