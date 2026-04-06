from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .integrations.openclaw_runtime import coordinator_finalize_hook, finalize_and_process_turn
from .integrations.openclaw_onboard import run_openclaw_onboard, render_onboard_report


def handle_integration_commands(*, args: Any, memory: Any, sidecar_parser: Any, openclaw_parser: Any) -> bool:
    """Handle sidecar/openclaw/integration migration commands.

    Returns True when handled, else False.
    """
    cmd = getattr(args, "command", None)

    if cmd == "sidecar":
        if args.sidecar_cmd == "finalize":
            result = coordinator_finalize_hook(
                root=args.root,
                session_id=args.session_id,
                turn_id=args.turn_id,
                transaction_id=args.transaction_id,
                trace_id=args.trace_id,
                user_query=args.user_query,
                assistant_final=args.assistant_final,
                trace_depth=args.trace_depth,
                origin=args.origin,
            )
            print(json.dumps(result, indent=2))
        elif args.sidecar_cmd == "turn":
            metadata = {
                "constraint_violation": bool(args.meta_constraint_violation),
                "wrong_transfer": bool(args.meta_wrong_transfer),
                "goal_carryover": bool(args.meta_goal_carryover),
                "store_full_text": (args.store_full_text == "true"),
            }
            result = finalize_and_process_turn(
                root=args.root,
                session_id=args.session_id,
                turn_id=args.turn_id,
                transaction_id=args.transaction_id,
                trace_id=args.trace_id,
                user_query=args.user_query,
                assistant_final=args.assistant_final,
                trace_depth=args.trace_depth,
                origin=args.origin,
                metadata=metadata,
            )
            print(json.dumps(result, indent=2))
        else:
            sidecar_parser.print_help()
        return True

    if cmd == "openclaw":
        if args.openclaw_cmd == "onboard":
            out = run_openclaw_onboard(
                openclaw_bin=args.openclaw_bin,
                plugin_dir=args.plugin_dir,
                replace_memory_core=bool(args.replace_memory_core),
                dry_run=bool(args.dry_run),
            )
            print(render_onboard_report(out))
            if not out.get("ok"):
                raise SystemExit(2)
        else:
            openclaw_parser.print_help()
        return True

    if cmd == "integrations-api-emit-turn":
        from core_memory.integrations.api import emit_turn_finalized_from_envelope

        envelope = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
        event_id = emit_turn_finalized_from_envelope(root=str(memory.root), envelope=envelope, strict=False)
        print(json.dumps({"ok": True, "event_id": event_id}, indent=2))
        return True

    if cmd == "integrations-migrate-rebuild-turn-indexes":
        from core_memory.integrations.migration import rebuild_turn_indexes

        print(json.dumps(rebuild_turn_indexes(root=str(memory.root)), indent=2))
        return True

    if cmd == "integrations-migrate-backfill-bead-session-ids":
        from core_memory.integrations.migration import backfill_bead_session_ids

        print(json.dumps(backfill_bead_session_ids(root=str(memory.root)), indent=2))
        return True

    return False
