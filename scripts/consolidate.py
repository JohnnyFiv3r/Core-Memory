#!/usr/bin/env python3
"""Core Memory consolidation utility implementation.

Canonical implementation module moved from repo root in V2P18.
Operational compatibility wrapper remains at `consolidate.py`.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root is importable when script is executed directly.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core_memory.store import MemoryStore
from core_memory.write_pipeline.orchestrate import run_rolling_window_pipeline
from core_memory.memory_engine import process_flush


def workspace_root() -> Path:
    return Path(os.environ.get("OPENCLAW_WORKSPACE", "/home/node/.openclaw/workspace"))


def store_root() -> str:
    return os.environ.get("CORE_MEMORY_ROOT") or os.environ.get("MEMBEADS_ROOT") or str(workspace_root() / "memory")


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def bead_render(bead: dict) -> str:
    lines = [f"- [{bead.get('type', 'context')}] {bead.get('title', '(untitled)')}"]
    for s in bead.get("summary", [])[:3]:
        lines.append(f"  - {s}")
    return "\n".join(lines)


def bead_sort_key(bead: dict):
    return bead.get("promoted_at") or bead.get("created_at") or ""


def render_rolling_window(
    store: MemoryStore, token_budget: int = 2000, max_beads: int = 200
) -> tuple[str, dict, list[str], list[str]]:
    index = store._read_json(store.beads_dir / "index.json")
    all_beads = [
        b for b in (index.get("beads") or {}).values() if str(b.get("status", "")).lower() != "superseded"
    ]

    candidates = sorted(all_beads, key=bead_sort_key, reverse=True)[:max_beads]
    promoted = [b for b in candidates if str(b.get("status", "")).lower() == "promoted"]
    non_promoted = [b for b in candidates if str(b.get("status", "")).lower() != "promoted"]

    lines = ["# Rolling Window (Core Memory)", ""]
    used = estimate_tokens("\n".join(lines))
    included = 0
    included_ids: list[str] = []

    for bead in candidates:
        block = bead_render(bead)
        cost = estimate_tokens(block + "\n")
        if used + cost > token_budget:
            continue
        lines.append(block)
        used += cost
        included += 1
        if bead.get("id"):
            included_ids.append(bead["id"])

    if included == 0:
        lines.append("- (no beads fit current token budget)")

    excluded_ids = [b.get("id") for b in candidates if b.get("id") and b.get("id") not in set(included_ids)]

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "token_budget": token_budget,
        "estimated_tokens_used": used,
        "candidate_beads": len(candidates),
        "candidate_promoted": len(promoted),
        "candidate_non_promoted": len(non_promoted),
        "included": included,
        "excluded": max(0, len(candidates) - included),
    }
    lines.append("")
    lines.append(f"_meta: {json.dumps(meta, ensure_ascii=False)}_")

    return "\n".join(lines) + "\n", meta, included_ids, excluded_ids


def cmd_consolidate(args, source: str = "flush_hook"):
    out = process_flush(
        root=store_root(),
        session_id=args.session,
        promote=bool(args.promote),
        token_budget=int(args.token_budget),
        max_beads=int(args.max_beads),
        source=source,
    )
    print(json.dumps(out))


def cmd_rolling_window(args):
    # Route through canonical rolling-window pipeline for consistency.
    out = run_rolling_window_pipeline(token_budget=int(args.token_budget), max_beads=int(args.max_beads), root=store_root())
    print(json.dumps(out))


def main():
    parser = argparse.ArgumentParser(prog="core-memory-consolidate")
    sub = parser.add_subparsers(dest="command", required=True)

    p1 = sub.add_parser("consolidate", help="Compact one session and refresh rolling window")
    p1.add_argument("--session", required=True)
    p1.add_argument("--promote", action="store_true", help="Opt-in: attempt candidate-only auto-promotion (requires CORE_MEMORY_AUTO_PROMOTE_ON_COMPACT=1)")
    p1.add_argument("--token-budget", type=int, default=int(os.environ.get("CORE_MEMORY_WINDOW_TOKENS", "2000")))
    p1.add_argument("--max-beads", type=int, default=200)

    p2 = sub.add_parser("rolling-window", help="Refresh rolling window only")
    p2.add_argument("--token-budget", type=int, default=int(os.environ.get("CORE_MEMORY_WINDOW_TOKENS", "2000")))
    p2.add_argument("--max-beads", type=int, default=200)

    p3 = sub.add_parser("flush", help="Manual admin flush trigger (canonical path)")
    p3.add_argument("--session", required=True)
    p3.add_argument("--promote", action="store_true")
    p3.add_argument("--token-budget", type=int, default=int(os.environ.get("CORE_MEMORY_WINDOW_TOKENS", "2000")))
    p3.add_argument("--max-beads", type=int, default=200)

    args = parser.parse_args()
    if args.command == "consolidate":
        cmd_consolidate(args, source="flush_hook")
    elif args.command == "flush":
        cmd_consolidate(args, source="admin_cli")
    else:
        cmd_rolling_window(args)


if __name__ == "__main__":
    main()
