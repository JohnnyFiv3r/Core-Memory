"""Compatibility adapter for routing mem-beads CLI calls to core_memory.

Phase 2 migration scaffold:
- keeps `mem-beads` public command stable
- allows opt-in core_memory execution via env flag
- translates supported legacy command shapes
"""

from __future__ import annotations

import os
import sys
from typing import List, Optional

SUPPORTED_LEGACY_COMMANDS = {"create", "query", "stats", "rebuild-index", "add", "rebuild"}


def _inject_root_if_missing(argv: List[str]) -> List[str]:
    """Inject --root from MEMBEADS_ROOT when core CLI doesn't receive one."""
    if "--root" in argv:
        return argv

    root = os.environ.get("MEMBEADS_ROOT") or os.environ.get("MEMBEADS_DIR")
    if not root:
        return argv

    if len(argv) <= 1:
        return argv + ["--root", root]
    return [argv[0], "--root", root, *argv[1:]]


def _find_command_index(argv: List[str]) -> Optional[int]:
    """Find first non-flag token after argv[0], accounting for --root value."""
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--root":
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return i
    return None


def _translate_legacy_to_core(argv: List[str]) -> List[str]:
    """Translate supported legacy mem-beads argv to core_memory argv."""
    argv = _inject_root_if_missing(argv)
    cmd_i = _find_command_index(argv)
    if cmd_i is None:
        return argv

    cmd = argv[cmd_i]
    if cmd not in SUPPORTED_LEGACY_COMMANDS:
        raise NotImplementedError(f"Command '{cmd}' not yet supported by core adapter")

    # normalize command aliases
    if cmd == "create":
        argv[cmd_i] = "add"
    elif cmd == "rebuild-index":
        argv[cmd_i] = "rebuild"

    # translate options for add/query
    if argv[cmd_i] == "add":
        out = argv[: cmd_i + 1]
        i = cmd_i + 1
        while i < len(argv):
            tok = argv[i]
            nxt = argv[i + 1] if i + 1 < len(argv) else None

            if tok == "--session" and nxt is not None:
                out.extend(["--session-id", nxt])
                i += 2
                continue

            if tok == "--tags" and nxt is not None:
                # legacy often uses comma-separated string
                parts = [p for p in nxt.split(",") if p]
                if parts:
                    out.append("--tags")
                    out.extend(parts)
                i += 2
                continue

            # strip unsupported legacy add flags for now
            if tok in {
                "--turn-refs",
                "--scope",
                "--authority",
                "--confidence",
                "--links",
                "--evidence",
                "--status",
                "--mechanism",
                "--impact",
                "--uncertainty",
                "--almost",
                "--rejected",
                "--risky",
                "--assumption",
                "--detail",
            }:
                i += 2 if nxt is not None and not nxt.startswith("-") else 1
                continue

            out.append(tok)
            i += 1

        return out

    if argv[cmd_i] == "query":
        out = argv[: cmd_i + 1]
        i = cmd_i + 1
        while i < len(argv):
            tok = argv[i]
            nxt = argv[i + 1] if i + 1 < len(argv) else None

            if tok == "--tag" and nxt is not None:
                out.extend(["--tags", nxt])
                i += 2
                continue

            # unsupported query flags in core cli right now
            if tok in {"--session", "--scope", "--full"}:
                i += 2 if tok != "--full" and nxt is not None and not nxt.startswith("-") else 1
                continue

            out.append(tok)
            i += 1

        return out

    return argv


def can_handle_with_core_adapter(argv: List[str] | None = None) -> bool:
    if argv is None:
        argv = sys.argv
    cmd_i = _find_command_index(_inject_root_if_missing(list(argv)))
    if cmd_i is None:
        return False
    return _inject_root_if_missing(list(argv))[cmd_i] in SUPPORTED_LEGACY_COMMANDS


def run_core_adapter(argv: List[str] | None = None) -> int:
    """Run core_memory CLI with mem-beads compatibility affordances."""
    if argv is None:
        argv = sys.argv

    from core_memory.cli import main as core_main

    translated = _translate_legacy_to_core(list(argv))

    old_argv = sys.argv
    try:
        sys.argv = translated
        core_main()
        return 0
    finally:
        sys.argv = old_argv
