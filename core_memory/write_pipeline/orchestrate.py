from __future__ import annotations

"""Write-pipeline orchestration helpers.

Transcript index-dump extraction path retired in V2P11.
Canonical runtime sequencing lives in `core_memory.memory_engine`.
"""

import os
from pathlib import Path

from .consolidate import run_session_consolidation, run_rolling_window_refresh


def get_memory_root() -> str:
    return os.getenv("CORE_MEMORY_ROOT", "./memory")


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
