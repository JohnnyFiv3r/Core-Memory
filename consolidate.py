#!/usr/bin/env python3
"""Core Memory consolidation utility.

Usage:
  python3 consolidate.py consolidate --session <id> [--promote]
  python3 consolidate.py rolling-window [--limit 20]
"""

import argparse
import json
import os
from pathlib import Path

from core_memory.store import MemoryStore


def workspace_root() -> Path:
    return Path(os.environ.get("OPENCLAW_WORKSPACE", "/home/node/.openclaw/workspace"))


def store_root() -> str:
    return os.environ.get("CORE_MEMORY_ROOT") or os.environ.get("MEMBEADS_ROOT") or str(workspace_root() / "memory")


def render_rolling_window(store: MemoryStore, limit: int = 20) -> str:
    promoted = store.query(status="promoted", limit=limit)
    lines = ["# Rolling Window (Core Memory)", ""]
    for b in promoted:
        lines.append(f"- [{b.get('type','context')}] {b.get('title','(untitled)')}")
    if not promoted:
        lines.append("- (no promoted beads yet)")
    return "\n".join(lines) + "\n"


def cmd_consolidate(args):
    store = MemoryStore(root=store_root())
    result = store.compact(session_id=args.session, promote=args.promote)

    # refresh rolling window file
    rw = render_rolling_window(store, limit=20)
    out = workspace_root() / "promoted-context.md"
    out.write_text(rw)

    print(json.dumps({"ok": True, "consolidate": result, "rolling_window": str(out)}))


def cmd_rolling_window(args):
    store = MemoryStore(root=store_root())
    rw = render_rolling_window(store, limit=args.limit)
    out = workspace_root() / "promoted-context.md"
    out.write_text(rw)
    print(json.dumps({"ok": True, "rolling_window": str(out)}))


def main():
    parser = argparse.ArgumentParser(prog="core-memory-consolidate")
    sub = parser.add_subparsers(dest="command", required=True)

    p1 = sub.add_parser("consolidate", help="Compact one session and refresh rolling window")
    p1.add_argument("--session", required=True)
    p1.add_argument("--promote", action="store_true")

    p2 = sub.add_parser("rolling-window", help="Refresh rolling window only")
    p2.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()
    if args.command == "consolidate":
        cmd_consolidate(args)
    else:
        cmd_rolling_window(args)


if __name__ == "__main__":
    main()
