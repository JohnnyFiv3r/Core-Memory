from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from core_memory.persistence.io_utils import append_jsonl, store_lock


def capture_turn_for_store(
    store: Any,
    *,
    role: str,
    content: str,
    tools_used: Optional[list] = None,
    user_message: str = "",
    session_id: str = "default",
) -> None:
    """Capture a turn in the turns archive."""
    turn = {
        "role": role,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tools_used": tools_used or [],
    }

    if user_message:
        turn["user_message"] = user_message

    turn_file = store.turns_dir / f"session-{session_id}.jsonl"
    with store_lock(store.root):
        append_jsonl(turn_file, turn)

    store.track_turn_processed(1)


def consolidate_for_store(store: Any, session_id: str = "default") -> dict:
    """Run session-end consolidation summary bead."""
    turn_file = store.turns_dir / f"session-{session_id}.jsonl"

    if turn_file.exists():
        with open(turn_file, "r", encoding="utf-8") as f:
            turns = [json.loads(line) for line in f if line.strip()]
        turn_count = len(turns)
    else:
        turn_count = 0

    bead_file = store.beads_dir / f"session-{session_id}.jsonl"

    if bead_file.exists():
        with open(bead_file, "r", encoding="utf-8") as f:
            beads = [json.loads(line) for line in f if line.strip()]
        bead_count = len(beads)
    else:
        bead_count = 0

    end_bead_id = store.add_bead(
        type="session_end",
        title=f"Session {session_id} summary",
        summary=[f"{turn_count} turns", f"{bead_count} events"],
        detail=f"Session {session_id} completed.",
        session_id=session_id,
        scope="project",
        tags=["session", session_id],
    )

    return {
        "session_id": session_id,
        "turns": turn_count,
        "events": bead_count,
        "end_bead": end_bead_id,
    }


__all__ = ["capture_turn_for_store", "consolidate_for_store"]
