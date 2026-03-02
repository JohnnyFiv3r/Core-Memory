"""Compatibility adapter for routing mem-beads CLI calls to core_memory.

Phase 2 migration scaffold:
- keeps `mem-beads` public command stable
- allows opt-in core_memory execution via env flag
"""

from __future__ import annotations

import os
import sys
from typing import List


def _inject_root_if_missing(argv: List[str]) -> List[str]:
    """Inject --root from MEMBEADS_ROOT when core CLI doesn't receive one."""
    if "--root" in argv:
        return argv

    root = os.environ.get("MEMBEADS_ROOT") or os.environ.get("MEMBEADS_DIR")
    if not root:
        return argv

    # keep command structure: mem-beads --root X <subcommand> ...
    if len(argv) <= 1:
        return argv + ["--root", root]
    return [argv[0], "--root", root, *argv[1:]]


def run_core_adapter(argv: List[str] | None = None) -> int:
    """Run core_memory CLI with mem-beads compatibility affordances."""
    if argv is None:
        argv = sys.argv

    from core_memory.cli import main as core_main

    patched = _inject_root_if_missing(list(argv))

    old_argv = sys.argv
    try:
        sys.argv = patched
        core_main()
        return 0
    finally:
        sys.argv = old_argv
