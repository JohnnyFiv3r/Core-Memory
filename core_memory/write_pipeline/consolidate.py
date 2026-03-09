from __future__ import annotations

from pathlib import Path

from core_memory.store import MemoryStore
from core_memory.rolling_surface import build_rolling_surface as build_rolling_window, write_rolling_surface as write_promoted_context


def run_session_consolidation(
    *,
    root: str,
    workspace_root: str | Path,
    session_id: str,
    promote: bool,
    token_budget: int,
    max_beads: int,
):
    memory = MemoryStore(root=root)
    comp = memory.compact(session_id=session_id, promote=bool(promote))

    text, meta, included_ids, excluded_ids = build_rolling_window(
        root=root,
        token_budget=int(token_budget),
        max_beads=int(max_beads),
    )
    out_path = write_promoted_context(workspace_root, text, meta=meta, included_ids=included_ids, excluded_ids=excluded_ids)

    hist = memory.compact(
        session_id=None,
        promote=False,
        only_bead_ids=excluded_ids,
        skip_bead_ids=included_ids,
    )

    return {
        "ok": True,
        "session": session_id,
        "promote": bool(promote),
        "compaction": comp,
        "historical_compaction": hist,
        "rolling_window": meta,
        "included_bead_ids": included_ids,
        "excluded_bead_ids": excluded_ids,
        "written": out_path,
    }


def run_rolling_window_refresh(*, root: str, workspace_root: str | Path, token_budget: int, max_beads: int):
    text, meta, included_ids, excluded_ids = build_rolling_window(
        root=root,
        token_budget=int(token_budget),
        max_beads=int(max_beads),
    )
    out_path = write_promoted_context(workspace_root, text, meta=meta, included_ids=included_ids, excluded_ids=excluded_ids)
    return {
        "ok": True,
        "rolling_window": meta,
        "included_bead_ids": included_ids,
        "excluded_bead_ids": excluded_ids,
        "written": out_path,
    }
