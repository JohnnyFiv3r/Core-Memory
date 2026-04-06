from __future__ import annotations

import argparse
from typing import Any


def rewrite_legacy_dev_memory_argv(argv: list[str]) -> list[str]:
    """Compatibility shim: treat `dev memory ...` as `memory ...`.

    Canonical memory commands live at top-level while preserving legacy
    automation entrypoints.
    """
    tokens = list(argv or [])
    cmd_idx = None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "--root":
            i += 2
            continue
        if t.startswith("--root="):
            i += 1
            continue
        if t.startswith("-"):
            i += 1
            continue
        cmd_idx = i
        break

    if cmd_idx is None:
        return tokens
    if tokens[cmd_idx] == "dev" and (cmd_idx + 1) < len(tokens) and tokens[cmd_idx + 1] == "memory":
        return tokens[:cmd_idx] + ["memory"] + tokens[cmd_idx + 2 :]
    return tokens


def ensure_group_subcommand_selected(args: Any, group_parsers: dict[str, argparse.ArgumentParser]) -> bool:
    """Print group help when grouped command is called without subcommand.

    Returns True when help was shown and caller should return early.
    """
    if getattr(args, "command", None) not in {"setup", "store", "recall", "inspect", "integrations", "ops", "dev"}:
        return False

    sub_name = {
        "setup": "setup_cmd",
        "store": "store_cmd",
        "recall": "recall_cmd",
        "inspect": "inspect_cmd",
        "integrations": "integrations_cmd",
        "ops": "ops_cmd",
        "dev": "dev_cmd",
    }[args.command]

    if not getattr(args, sub_name, None):
        group_parsers[args.command].print_help()
        return True
    return False


def apply_grouped_aliases(args: Any, *, openclaw_group_parser: argparse.ArgumentParser) -> bool:
    """Map grouped/compat command trees into canonical handler commands.

    Returns True when help was shown and caller should return early.
    """
    if args.command == "setup":
        if args.setup_cmd == "init":
            return False
        if args.setup_cmd == "doctor":
            args.command = "doctor"
        elif args.setup_cmd == "paths":
            return False

    if args.command == "store":
        if args.store_cmd == "add":
            args.command = "add"
        elif args.store_cmd == "stats":
            args.command = "stats"
        elif args.store_cmd == "compact":
            args.command = "compact"
        elif args.store_cmd == "uncompact":
            args.command = "uncompact"
        elif args.store_cmd == "consolidate":
            args.command = "consolidate"
        elif args.store_cmd == "rolling-window":
            args.command = "rolling-window"

    if args.command == "recall":
        if args.recall_cmd == "search":
            args.command = "memory"
            args.memory_cmd = "search"
        elif args.recall_cmd == "heads":
            args.command = "heads"
        elif args.recall_cmd == "trace":
            args.command = "memory"
            args.memory_cmd = "trace"

    if args.command == "inspect":
        if args.inspect_cmd == "list":
            args.command = "query"
        elif args.inspect_cmd == "stats":
            args.command = "stats"
        elif args.inspect_cmd == "health":
            args.command = "doctor"

    if args.command == "integrations":
        if args.integrations_cmd == "openclaw":
            if not getattr(args, "integrations_openclaw_cmd", None):
                openclaw_group_parser.print_help()
                return True
            if args.integrations_openclaw_cmd == "onboard":
                args.command = "openclaw"
                args.openclaw_cmd = "onboard"
        elif args.integrations_cmd == "api":
            if args.integrations_api_cmd == "emit-turn":
                args.command = "integrations-api-emit-turn"
        elif args.integrations_cmd == "migrate":
            if args.integrations_migrate_cmd == "rebuild-turn-indexes":
                args.command = "integrations-migrate-rebuild-turn-indexes"
            elif args.integrations_migrate_cmd == "backfill-bead-session-ids":
                args.command = "integrations-migrate-backfill-bead-session-ids"

    if args.command == "ops":
        if args.ops_cmd == "doctor":
            args.command = "doctor"
        elif args.ops_cmd == "rebuild":
            args.command = "rebuild"
        elif args.ops_cmd == "archive-index-rebuild":
            args.command = "metrics"
            args.metrics_cmd = "archive-index-rebuild"
        elif args.ops_cmd == "graph-sync":
            args.command = "graph"
            args.graph_cmd = "sync-structural"
            args.apply = True
            args.strict = False
        elif args.ops_cmd == "jobs-status":
            args.command = "async-jobs-status"
        elif args.ops_cmd == "jobs-enqueue":
            args.command = "async-jobs-enqueue"
        elif args.ops_cmd == "jobs-run":
            args.command = "async-jobs-run"

    return False
