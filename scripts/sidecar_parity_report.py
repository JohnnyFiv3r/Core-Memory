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


def collect_turns(session_file: Path, limit: int) -> list[dict]:
    rows = []
    with open(session_file, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    turns = []
    i = 0
    while i < len(rows):
        r = rows[i]
        if r.get("type") != "message":
            i += 1
            continue
        m = r.get("message", {})
        if m.get("role") != "user":
            i += 1
            continue

        tid = r.get("id")
        user_text = _extract_text(m.get("content"))
        if not tid or not user_text:
            i += 1
            continue

        j = i + 1
        has_assistant = False
        while j < len(rows):
            r2 = rows[j]
            if r2.get("type") != "message":
                j += 1
                continue
            m2 = r2.get("message", {})
            role2 = m2.get("role")
            if role2 == "user":
                break
            if role2 == "assistant" and _extract_text(m2.get("content")):
                has_assistant = True
            j += 1

        turns.append({"turn_id": tid, "has_assistant_final": has_assistant})
        i = j

    return turns[-limit:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/home/node/.openclaw/workspace/memory")
    ap.add_argument("--sessions-json", default="/home/node/.openclaw/agents/main/sessions/sessions.json")
    ap.add_argument("--sessions-dir", default="/home/node/.openclaw/agents/main/sessions")
    ap.add_argument("--window", type=int, default=100)
    ap.add_argument("--diagnose", action="store_true", help="Include uncovered turn diagnostics")
    ap.add_argument("--finalized-only", action="store_true", help="Exclude turns without finalized assistant response")
    args = ap.parse_args()

    sid = load_main_session_id(Path(args.sessions_json))
    if not sid:
        raise SystemExit("Could not resolve active main session id")

    turn_rows = collect_turns(Path(args.sessions_dir) / f"{sid}.jsonl", args.window)
    if args.finalized_only:
        turn_rows = [t for t in turn_rows if t.get("has_assistant_final", False)]
    turns = [t["turn_id"] for t in turn_rows]

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

    if args.diagnose:
        missing = []
        for tr in turn_rows:
            tid = tr["turn_id"]
            rec = state.get(f"main:{tid}")
            done = bool(rec and rec.get("status") == "done")
            if done:
                continue
            reason = "no_memory_pass_state"
            if rec and rec.get("status") != "done":
                reason = f"state_{rec.get('status')}"
            if not tr.get("has_assistant_final", False):
                reason = "no_assistant_final_yet"
            missing.append({"turn_id": tid, "reason": reason})
        report["missing_turns"] = missing[:20]
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
