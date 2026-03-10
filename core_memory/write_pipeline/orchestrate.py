from __future__ import annotations

"""Legacy transcript/backfill orchestration helpers.

Status: compatibility path (non-canonical runtime lifecycle).
Canonical runtime sequencing lives in `core_memory.memory_engine`.
"""

import os
from pathlib import Path

from .transcript_source import find_transcript
from .marker_parse import extract_beads_from_transcript
from .persist import write_beads_via_cli
from .idempotency import already_extracted, write_extracted_marker
from .consolidate import run_session_consolidation, run_rolling_window_refresh


def get_memory_root() -> str:
    return os.getenv("CORE_MEMORY_ROOT", "./memory")


def run_extract_pipeline(*, session_id: str | None, consolidate: bool) -> dict:
    root = get_memory_root()
    transcript_path, sid = find_transcript(session_id)

    if os.getenv("CORE_MEMORY_EXTRACT_ONCE", "1") != "0" and already_extracted(root, sid):
        return {"ok": True, "session_id": sid, "skipped": True, "reason": "already_extracted"}

    beads = extract_beads_from_transcript(transcript_path)
    written, failed = write_beads_via_cli(beads, root=root)

    write_extracted_marker(root, sid, str(transcript_path), written)

    out = {
        "ok": True,
        "session_id": sid,
        "transcript": str(transcript_path),
        "extracted": len(beads),
        "written": written,
        "failed": failed,
    }

    if consolidate:
        c = run_session_consolidation(
            root=root,
            workspace_root=Path(__file__).resolve().parents[2],
            session_id=sid,
            promote=False,
            token_budget=3000,
            max_beads=80,
        )
        out["consolidation"] = c

    return out


def run_consolidate_pipeline(
    *,
    session_id: str,
    promote: bool,
    token_budget: int,
    max_beads: int,
    root: str | None = None,
    workspace_root: str | None = None,
) -> dict:
    root_final = str(root or get_memory_root())
    return run_session_consolidation(
        root=root_final,
        workspace_root=str(workspace_root or root_final),
        session_id=session_id,
        promote=promote,
        token_budget=token_budget,
        max_beads=max_beads,
    )


def run_rolling_window_pipeline(*, token_budget: int, max_beads: int) -> dict:
    return run_rolling_window_refresh(
        root=get_memory_root(),
        workspace_root=Path(__file__).resolve().parents[2],
        token_budget=token_budget,
        max_beads=max_beads,
    )
