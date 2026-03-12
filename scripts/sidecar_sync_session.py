#!/usr/bin/env python3
"""Sync OpenClaw main session transcript into Core Memory sidecar events.

This is a bridge until coordinator-native finalize hook is wired in runtime.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core_memory.openclaw_integration import coordinator_finalize_hook, process_pending_memory_events
from core_memory.event_worker import SidecarPolicy


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for c in content:
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(c.get("text", ""))
    return "\n".join(p for p in parts if p).strip()


def load_main_session_id(sessions_json: Path) -> str:
    data = json.loads(sessions_json.read_text(encoding="utf-8"))
    main = data.get("agent:main:main") or {}
    sid = main.get("sessionId")
    if not sid:
        raise RuntimeError("Could not resolve main sessionId from sessions.json")
    return sid


def iter_turns(session_file: Path):
    rows = []
    with open(session_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

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

        user_id = r.get("id") or f"line-{i}"
        user_query = _extract_text(m.get("content"))
        j = i + 1
        final_assistant = ""
        while j < len(rows):
            r2 = rows[j]
            if r2.get("type") != "message":
                j += 1
                continue
            m2 = r2.get("message", {})
            role2 = m2.get("role")
            if role2 == "user":
                break
            if role2 == "assistant":
                txt = _extract_text(m2.get("content"))
                if txt:
                    final_assistant = txt
            j += 1

        if final_assistant:
            yield {
                "turn_id": user_id,
                "user_query": user_query,
                "assistant_final": final_assistant,
            }
        i = j


def resolve_core_session_id(*, openclaw_session_id: str, core_session_id: str | None, collapse_to_main: bool) -> str:
    """Resolve Core Memory session target for transcript sync.

    Default behavior preserves true OpenClaw session boundaries.
    Compatibility mode can collapse all sync into `main`.
    """
    if core_session_id and str(core_session_id).strip():
        return str(core_session_id).strip()
    if collapse_to_main:
        return "main"
    return str(openclaw_session_id or "main").strip() or "main"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/home/node/.openclaw/workspace/memory")
    ap.add_argument("--sessions-json", default="/home/node/.openclaw/agents/main/sessions/sessions.json")
    ap.add_argument("--sessions-dir", default="/home/node/.openclaw/agents/main/sessions")
    ap.add_argument("--max-turns", type=int, default=200)
    ap.add_argument("--emit-only", action="store_true")
    ap.add_argument("--core-session-id", default=None, help="Force Core Memory session_id (override)")
    ap.add_argument("--collapse-to-main", action="store_true", help="Compatibility mode: force all synced turns into session_id=main")
    args = ap.parse_args()

    sid = load_main_session_id(Path(args.sessions_json))
    core_sid = resolve_core_session_id(
        openclaw_session_id=sid,
        core_session_id=args.core_session_id,
        collapse_to_main=bool(args.collapse_to_main),
    )
    session_file = Path(args.sessions_dir) / f"{sid}.jsonl"

    emitted = 0
    for t in list(iter_turns(session_file))[-args.max_turns :]:
        out = coordinator_finalize_hook(
            root=args.root,
            session_id=core_sid,
            turn_id=t["turn_id"],
            transaction_id=f"tx-{t['turn_id']}",
            trace_id=f"tr-{t['turn_id']}",
            user_query=t["user_query"],
            assistant_final=t["assistant_final"],
            trace_depth=0,
            origin="USER_TURN",
        )
        if out.get("emitted"):
            emitted += 1

    processed = {"processed": 0, "failed": 0}
    if not args.emit_only:
        processed = process_pending_memory_events(
            args.root,
            max_events=max(args.max_turns * 2, 50),
            policy=SidecarPolicy(create_threshold=0.0),
        )

    print(json.dumps({
        "ok": True,
        "openclaw_session_id": sid,
        "core_session_id": core_sid,
        "emitted": emitted,
        **processed,
    }, indent=2))


if __name__ == "__main__":
    main()
