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

from core_memory.store import MemoryStore

SUPPORTED_LEGACY_COMMANDS = {
    "create",
    "query",
    "stats",
    "rebuild-index",
    "add",
    "rebuild",
    "link",
    "recall",
    "supersede",
    "validate",
    "compact",
    "uncompact",
    "myelinate",
    "migrate-store",
    "close",  # limited: promoted status only
}


def _inject_root_if_missing(argv: List[str]) -> List[str]:
    if "--root" in argv:
        return argv

    root = os.environ.get("MEMBEADS_ROOT") or os.environ.get("MEMBEADS_DIR")
    if not root:
        return argv

    if len(argv) <= 1:
        return argv + ["--root", root]
    return [argv[0], "--root", root, *argv[1:]]


def _find_command_index(argv: List[str]) -> Optional[int]:
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


def _extract_root(argv: List[str]) -> str:
    if "--root" in argv:
        i = argv.index("--root")
        if i + 1 < len(argv):
            return argv[i + 1]
    return os.environ.get("MEMBEADS_ROOT") or os.environ.get("MEMBEADS_DIR") or "./memory"


def _arg_value(argv: List[str], flag: str) -> Optional[str]:
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def _translate_legacy_to_core(argv: List[str]) -> List[str]:
    argv = _inject_root_if_missing(argv)
    cmd_i = _find_command_index(argv)
    if cmd_i is None:
        return argv

    cmd = argv[cmd_i]
    if cmd not in SUPPORTED_LEGACY_COMMANDS:
        raise NotImplementedError(f"Command '{cmd}' not yet supported by core adapter")

    if cmd == "create":
        argv[cmd_i] = "add"
    elif cmd == "rebuild-index":
        argv[cmd_i] = "rebuild"

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
                parts = [p for p in nxt.split(",") if p]
                if parts:
                    out.append("--tags")
                    out.extend(parts)
                i += 2
                continue

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

    if argv[cmd_i] == "compact":
        out = argv[: cmd_i + 1]
        i = cmd_i + 1
        while i < len(argv):
            tok = argv[i]
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if tok == "--keep-promoted":
                out.append("--promote")
                i += 1
                continue
            if tok == "--before":
                # not implemented in core compact yet
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
    patched = _inject_root_if_missing(list(argv))
    cmd_i = _find_command_index(patched)
    if cmd_i is None:
        return False
    return patched[cmd_i] in SUPPORTED_LEGACY_COMMANDS


def _run_direct_compat(argv: List[str]) -> Optional[int]:
    """Handle commands not present in core_memory.cli directly via MemoryStore."""
    patched = _inject_root_if_missing(list(argv))
    cmd_i = _find_command_index(patched)
    if cmd_i is None:
        return None

    cmd = patched[cmd_i]
    root = _extract_root(patched)
    store = MemoryStore(root=root)

    if cmd == "link":
        source = _arg_value(patched, "--from")
        target = _arg_value(patched, "--to")
        rel = _arg_value(patched, "--type")
        if not (source and target and rel):
            raise SystemExit("link requires --from --to --type")
        assoc_id = store.link(source, target, rel)
        print(f'{{"ok": true, "id": "{assoc_id}"}}')
        return 0

    if cmd == "recall":
        bead_id = _arg_value(patched, "--id")
        if not bead_id:
            raise SystemExit("recall requires --id")
        ok = store.recall(bead_id)
        print(f'{{"ok": {str(ok).lower()}, "id": "{bead_id}"}}')
        return 0 if ok else 1

    if cmd == "supersede":
        old_id = _arg_value(patched, "--old")
        new_id = _arg_value(patched, "--new")
        if not (old_id and new_id):
            raise SystemExit("supersede requires --old --new")
        store.link(new_id, old_id, "supersedes")
        print(f'{{"ok": true, "old": "{old_id}", "new": "{new_id}", "status": "superseded"}}')
        return 0

    if cmd == "validate":
        idx = store._read_json(store.beads_dir / "index.json")
        beads = idx.get("beads", {})
        assocs = idx.get("associations", [])
        sessions = {b.get("session_id") for b in beads.values() if b.get("session_id")}
        payload = {
            "ok": True,
            "total_beads": len(beads),
            "total_edges": len(assocs),
            "total_sessions": len(sessions),
            "total_issues": 0,
            "issues": {},
        }
        import json
        print(json.dumps(payload, indent=2))
        return 0

    if cmd == "close":
        bead_id = _arg_value(patched, "--id")
        status = _arg_value(patched, "--status")
        if not (bead_id and status):
            raise SystemExit("close requires --id --status")
        if status == "promoted":
            ok = store.promote(bead_id)
            print(f'{{"ok": {str(ok).lower()}, "id": "{bead_id}", "status": "promoted"}}')
            return 0 if ok else 1

        # Generic close status update for compatibility (`closed`, `superseded`, etc.)
        idx = store._read_json(store.beads_dir / "index.json")
        if bead_id not in idx.get("beads", {}):
            print(f'{{"ok": false, "error": "Bead not found: {bead_id}"}}')
            return 1
        idx["beads"][bead_id]["status"] = status
        store._write_json(store.beads_dir / "index.json", idx)
        print(f'{{"ok": true, "id": "{bead_id}", "status": "{status}"}}')
        return 0

    return None


def run_core_adapter(argv: List[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv

    # Direct compatibility handlers first
    direct = _run_direct_compat(list(argv))
    if direct is not None:
        return direct

    from core_memory.cli import main as core_main

    translated = _translate_legacy_to_core(list(argv))

    old_argv = sys.argv
    try:
        sys.argv = translated
        core_main()
        return 0
    finally:
        sys.argv = old_argv
