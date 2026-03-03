#!/usr/bin/env python3
"""Sidecar parity report: transcript turns vs memory pass coverage vs bead writes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text").strip()


def load_main_session_id(sessions_json: Path) -> str:
    data = json.loads(sessions_json.read_text(encoding="utf-8"))
    return (data.get("agent:main:main") or {}).get("sessionId")


def collect_turn_ids(session_file: Path, limit: int) -> list[str]:
    turns = []
    with open(session_file, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("type") != "message":
                continue
            msg = row.get("message", {})
            if msg.get("role") != "user":
                continue
            if _extract_text(msg.get("content")):
                turns.append(row.get("id"))
    return [t for t in turns if t][-limit:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/home/node/.openclaw/workspace/memory")
    ap.add_argument("--sessions-json", default="/home/node/.openclaw/agents/main/sessions/sessions.json")
    ap.add_argument("--sessions-dir", default="/home/node/.openclaw/agents/main/sessions")
    ap.add_argument("--window", type=int, default=100)
    args = ap.parse_args()

    sid = load_main_session_id(Path(args.sessions_json))
    if not sid:
        raise SystemExit("Could not resolve active main session id")

    turns = collect_turn_ids(Path(args.sessions_dir) / f"{sid}.jsonl", args.window)

    state_file = Path(args.root) / ".beads" / "events" / "memory-pass-state.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}

    idx_file = Path(args.root) / ".beads" / "index.json"
    idx = json.loads(idx_file.read_text(encoding="utf-8")) if idx_file.exists() else {"beads": {}}
    beads = list((idx.get("beads") or {}).values())

    pass_done = 0
    for tid in turns:
        k = f"main:{tid}"
        rec = state.get(k)
        if rec and rec.get("status") == "done":
            pass_done += 1

    covered_turns = set()
    for b in beads:
        for tid in (b.get("source_turn_ids") or []):
            if tid in turns:
                covered_turns.add(tid)
    beads_with_source = len(covered_turns)

    report = {
        "ok": True,
        "session_id": sid,
        "window_turns": len(turns),
        "memory_pass_done": pass_done,
        "memory_pass_coverage": round((pass_done / len(turns)), 4) if turns else 0.0,
        "beads_with_source_turn_in_window": beads_with_source,
        "bead_coverage_per_turn": round((beads_with_source / len(turns)), 4) if turns else 0.0,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
