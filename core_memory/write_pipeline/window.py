from __future__ import annotations

# Transitional compatibility wrapper (P7C deprecation marker).
# Canonical rolling surface owner: core_memory.rolling_surface
# Status: deprecated shim; retained for migration safety.

LEGACY_SHIM = True
SHIM_REPLACEMENT = "core_memory.rolling_surface"

from core_memory.rolling_surface import (
    estimate_tokens,
    render_bead,
    build_rolling_surface,
    write_rolling_surface,
)


def build_rolling_window(root: str, token_budget: int = 3000, max_beads: int = 80):
    return build_rolling_surface(root=root, token_budget=token_budget, max_beads=max_beads)


def write_promoted_context(workspace_root, text: str, meta: dict | None = None, included_ids: list[str] | None = None, excluded_ids: list[str] | None = None) -> str:
    return write_rolling_surface(
        workspace_root=workspace_root,
        text=text,
        meta=meta,
        included_ids=included_ids,
        excluded_ids=excluded_ids,
    )
