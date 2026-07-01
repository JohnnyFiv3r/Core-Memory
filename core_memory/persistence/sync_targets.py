from __future__ import annotations

import logging
import os
from importlib import import_module
from typing import Any

_log = logging.getLogger(__name__)


def _obsidian_sync_target() -> Any:
    module = import_module("core_memory.integrations.obsidian")
    return module.ObsidianSyncTarget.from_env()


def create_sync_targets() -> list[Any]:
    """Instantiate configured persistence sync targets.

    Integration modules are loaded dynamically so persistence code can mirror
    sync work without importing upward into integration packages.
    """
    targets_env = (os.environ.get("CORE_MEMORY_SYNC_TARGETS") or "").strip().lower()
    if not targets_env or targets_env == "none":
        return []

    targets: list[Any] = []
    for name in [t.strip() for t in targets_env.split(",") if t.strip()]:
        if name == "obsidian":
            try:
                targets.append(_obsidian_sync_target())
            except Exception as exc:
                _log.warning("obsidian sync target init failed: %s", exc)
    return targets
