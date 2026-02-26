#!/usr/bin/env python3
"""
mem-beads consolidate: Session-end consolidation script.

Called during pre-compaction memory flush. Performs:
1. Reads all beads for a session
2. Generates a session_end summary bead (written by the calling agent, not this script)
3. Identifies promotion candidates
4. Compacts non-promoted beads
5. Regenerates the rolling window context file

Usage:
  consolidate.py --session <id> [--promote] [--window-size 10] [--budget 5000]
  consolidate.py --rolling-window [--window-size 10] [--budget 5000]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Import from mem_beads
sys.path.insert(0, os.path.dirname(__file__))
from mem_beads import (
    BEADS_DIR, PROMOTION_ELIGIBLE,
    load_index, save_index, read_all_beads, read_beads,
    _session_file, append_bead, make_bead, FileLock
)

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))

ROLLING_WINDOW_FILE = os.path.join(WORKSPACE, "promoted-context.md")

MEMORY_FILE = os.path.join(WORKSPACE, "MEMORY.md")

# Marker for the rolling window section in MEMORY.md
RW_SECTION_START = "<!-- mem-beads:rolling-window:start -->"
RW_SECTION_END = "<!-- mem-beads:rolling-window:end -->"

DEFAULT_WINDOW_SIZE = 10  # sessions
DEFAULT_TOKEN_BUDGET = 5000  # tokens for rolling window


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4


def get_session_beads(session_id: str) -> list[dict]:
    """Get all beads for a session, ordered by creation time."""
    filepath = _session_file(session_id)
    beads = []
    for b in read_beads(filepath):
        if not b.get("event") and b.get("id"):
            beads.append(b)
    beads.sort(key=lambda b: b.get("created_at", ""))
    return beads


def identify_promotion_candidates(beads: list[dict], index: dict | None = None) -> list[dict]:
    """Identify beads eligible for promotion based on type and confidence."""
    if index is None:
        index = load_index()
    candidates = []
    for bead in beads:
        bead_type = bead.get("type", "")
        # Check index for real status (JSONL is append-only, status updates are in index)
        bead_id = bead.get("id", "")
        idx_meta = index.get("beads", {}).get(bead_id, {})
        status = idx_meta.get("status", bead.get("status", "open"))
        confidence = bead.get("confidence", 0.5)

        # Skip already promoted/compacted/superseded
        if status in ("promoted", "compacted", "superseded"):
            continue

        # Must be promotion-eligible type
        if bead_type not in PROMOTION_ELIGIBLE:
            continue

        # Primary promotion targets: lessons, decisions, precedents, outcomes
        # These auto-promote at confidence >= 0.8
        primary = {"lesson", "decision", "precedent", "outcome", "promoted_lesson", "promoted_decision"}
        if bead_type in primary and confidence >= 0.8:
            candidates.append(bead)
            continue

        # Secondary targets: goals, evidence, context
        # These need higher confidence (0.9+) or user_confirmed authority
        if bead.get("authority") == "user_confirmed":
            candidates.append(bead)
            continue

        if confidence >= 0.9:
            candidates.append(bead)

    return candidates


def compact_session(session_id: str, keep_promoted: bool = True) -> int:
    """Compact all non-promoted beads in a session."""
    index = load_index()
    compacted = 0

    for bead_id, meta in index["beads"].items():
        if meta.get("session_id") != session_id:
            continue
        if meta["status"] in ("compacted", "promoted"):
            continue
        if keep_promoted and meta["status"] == "promoted":
            continue

        index["beads"][bead_id]["status"] = "compacted"
        index["beads"][bead_id]["compacted_at"] = datetime.now(timezone.utc).isoformat()
        compacted += 1

    save_index(index)
    return compacted


def get_recent_sessions(window_size: int = DEFAULT_WINDOW_SIZE) -> list[str]:
    """Get the most recent session IDs ordered by last bead creation time."""
    index = load_index()
    sessions = {}

    for bead_id, meta in index["beads"].items():
        sid = meta.get("session_id")
        if not sid:
            continue
        ts = meta.get("created_at", "")
        if sid not in sessions or ts > sessions[sid]:
            sessions[sid] = ts

    # Sort by most recent activity
    sorted_sessions = sorted(sessions.items(), key=lambda x: x[1], reverse=True)
    return [sid for sid, _ in sorted_sessions[:window_size]]


def get_session_summary(session_id: str) -> dict | None:
    """Get the session_end summary bead for a session, or build one from beads."""
    beads = get_session_beads(session_id)
    if not beads:
        return None

    # Look for an explicit session_end bead
    for b in reversed(beads):
        if b.get("type") == "session_end":
            return b

    # No session_end bead — build a quick summary from non-trivial beads
    notable = [b for b in beads if b.get("type") not in ("session_start", "checkpoint")]
    if not notable:
        return None

    return {
        "session_id": session_id,
        "type": "session_end",
        "title": f"Session {session_id} ({len(beads)} beads)",
        "summary": [
            f"{b['type']}: {b.get('title', 'untitled')}"
            for b in notable[:10]  # cap at 10 for token budget
        ],
        "created_at": beads[-1].get("created_at", ""),
    }


def generate_rolling_window(window_size: int = DEFAULT_WINDOW_SIZE, budget: int = DEFAULT_TOKEN_BUDGET) -> str:
    """Generate the rolling window markdown from recent sessions."""
    sessions = get_recent_sessions(window_size)
    if not sessions:
        return "# Rolling Window\n\nNo sessions recorded yet.\n"

    lines = ["# Rolling Window — Recent Session Context\n"]
    lines.append(f"_Last {len(sessions)} sessions, auto-generated by mem-beads consolidate._\n")

    total_tokens = estimate_tokens("\n".join(lines))
    index = load_index()

    for sid in sessions:
        summary = get_session_summary(sid)
        if not summary:
            continue

        # Build session section
        section = [f"\n## {summary.get('title', sid)}"]
        section.append(f"_Created: {summary.get('created_at', 'unknown')}_\n")

        if summary.get("summary"):
            for point in summary["summary"]:
                section.append(f"- {point}")

        # Add promoted beads for this session
        promoted = []
        for bead_id, meta in index["beads"].items():
            if meta.get("session_id") == sid and meta.get("status") == "promoted":
                promoted.append(meta)

        if promoted:
            section.append(f"\n**Promoted ({len(promoted)}):**")
            for p in promoted:
                section.append(f"- [{p['type']}] {p.get('title', 'untitled')}")

        section_text = "\n".join(section)
        section_tokens = estimate_tokens(section_text)

        if total_tokens + section_tokens > budget:
            lines.append(f"\n_({len(sessions) - sessions.index(sid)} older sessions omitted for token budget)_")
            break

        lines.append(section_text)
        total_tokens += section_tokens

    result = "\n".join(lines) + "\n"
    lines.append(f"\n_Total estimated tokens: ~{estimate_tokens(result)}_")
    return "\n".join(lines) + "\n"


def inject_rolling_window_into_memory(window_md: str) -> bool:
    """Inject/replace the rolling window section in MEMORY.md."""
    section = f"\n{RW_SECTION_START}\n{window_md}{RW_SECTION_END}\n"

    if not os.path.exists(MEMORY_FILE):
        return False

    with open(MEMORY_FILE, "r") as f:
        content = f.read()

    if RW_SECTION_START in content and RW_SECTION_END in content:
        # Replace existing section
        start = content.index(RW_SECTION_START)
        end = content.index(RW_SECTION_END) + len(RW_SECTION_END)
        # Include surrounding newlines
        while start > 0 and content[start - 1] == "\n":
            start -= 1
        while end < len(content) and content[end] == "\n":
            end += 1
        content = content[:start] + section + content[end:]
    else:
        # Append at end
        content = content.rstrip() + "\n" + section

    with open(MEMORY_FILE, "w") as f:
        f.write(content)
    return True


def cmd_consolidate(args):
    """Consolidate a session's beads."""
    session_id = args.session
    beads = get_session_beads(session_id)

    if not beads:
        print(json.dumps({"ok": True, "session": session_id, "beads": 0, "message": "No beads found"}))
        return

    result = {
        "ok": True,
        "session": session_id,
        "total_beads": len(beads),
    }

    # Identify promotion candidates
    candidates = identify_promotion_candidates(beads)
    result["promotion_candidates"] = [
        {"id": b["id"], "type": b["type"], "title": b.get("title", "")}
        for b in candidates
    ]

    # Auto-promote if requested
    if args.promote and candidates:
        index = load_index()
        for b in candidates:
            if b["id"] in index["beads"]:
                index["beads"][b["id"]]["status"] = "promoted"
                index["beads"][b["id"]]["promoted_at"] = datetime.now(timezone.utc).isoformat()
        save_index(index)
        result["promoted"] = len(candidates)

    # Compact non-promoted beads
    compacted = compact_session(session_id, keep_promoted=True)
    result["compacted"] = compacted

    # Regenerate rolling window
    window = generate_rolling_window(
        window_size=int(args.window_size),
        budget=int(args.budget)
    )
    with open(ROLLING_WINDOW_FILE, "w") as f:
        f.write(window)
    result["rolling_window_tokens"] = estimate_tokens(window)
    result["rolling_window_file"] = ROLLING_WINDOW_FILE

    # Inject into MEMORY.md for auto-loading at session start
    injected = inject_rolling_window_into_memory(window)
    result["injected_into_memory"] = injected

    print(json.dumps(result, indent=2))


def cmd_rolling_window(args):
    """Regenerate just the rolling window file."""
    window = generate_rolling_window(
        window_size=int(args.window_size),
        budget=int(args.budget)
    )
    with open(ROLLING_WINDOW_FILE, "w") as f:
        f.write(window)

    print(json.dumps({
        "ok": True,
        "tokens": estimate_tokens(window),
        "file": ROLLING_WINDOW_FILE,
        "sessions": len(get_recent_sessions(int(args.window_size))),
    }, indent=2))


def cmd_candidates(args):
    """List promotion candidates across all sessions or a specific one."""
    if args.session:
        beads = get_session_beads(args.session)
    else:
        beads = [b for b in read_all_beads() if not b.get("event")]

    candidates = identify_promotion_candidates(beads)
    print(json.dumps([
        {"id": b["id"], "type": b["type"], "title": b.get("title", ""), "confidence": b.get("confidence")}
        for b in candidates
    ], indent=2))


def main():
    parser = argparse.ArgumentParser(prog="mem-beads-consolidate")
    sub = parser.add_subparsers(dest="command", required=True)

    # consolidate
    p = sub.add_parser("consolidate", help="Consolidate a session's beads")
    p.add_argument("--session", required=True)
    p.add_argument("--promote", action="store_true", help="Auto-promote candidates")
    p.add_argument("--window-size", default=str(DEFAULT_WINDOW_SIZE))
    p.add_argument("--budget", default=str(DEFAULT_TOKEN_BUDGET))

    # rolling-window
    p = sub.add_parser("rolling-window", help="Regenerate rolling window file")
    p.add_argument("--window-size", default=str(DEFAULT_WINDOW_SIZE))
    p.add_argument("--budget", default=str(DEFAULT_TOKEN_BUDGET))

    # candidates
    p = sub.add_parser("candidates", help="List promotion candidates")
    p.add_argument("--session")

    args = parser.parse_args()
    commands = {
        "consolidate": cmd_consolidate,
        "rolling-window": cmd_rolling_window,
        "candidates": cmd_candidates,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
