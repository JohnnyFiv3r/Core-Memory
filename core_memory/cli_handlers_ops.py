from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .runtime.jobs import async_jobs_status, enqueue_async_job, run_async_jobs


def _load_json_object(path: str, *, code_prefix: str, flag_name: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": f"{code_prefix}_invalid_json",
                        "message": f"--{flag_name} must be valid JSON object",
                        "detail": str(exc),
                    },
                },
                indent=2,
            )
        )
        raise SystemExit(2)
    if not isinstance(payload, dict):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": f"{code_prefix}_not_object",
                        "message": f"--{flag_name} must contain a JSON object",
                    },
                },
                indent=2,
            )
        )
        raise SystemExit(2)
    return payload


def handle_ops_commands(*, args: Any, memory: Any) -> bool:
    cmd = getattr(args, "command", None)

    if cmd == "async-jobs-status":
        print(json.dumps(async_jobs_status(memory.root), indent=2))
        return True

    if cmd == "async-jobs-enqueue":
        event: dict[str, Any] = {}
        ctx: dict[str, Any] = {}

        if getattr(args, "event_file", None):
            event = _load_json_object(str(args.event_file), code_prefix="event_file", flag_name="event-file")
        if getattr(args, "ctx_file", None):
            ctx = _load_json_object(str(args.ctx_file), code_prefix="ctx_file", flag_name="ctx-file")

        if getattr(args, "run_id", None):
            event["runId"] = str(args.run_id)
        if getattr(args, "session_id", None):
            ctx["sessionId"] = str(args.session_id)

        out = enqueue_async_job(memory.root, kind=str(args.kind), event=event, ctx=ctx)
        print(json.dumps(out, indent=2))
        if not out.get("ok"):
            raise SystemExit(2)
        return True

    if cmd == "async-jobs-run":
        out = run_async_jobs(
            memory.root,
            run_semantic=not bool(args.no_semantic),
            max_compaction=int(args.max_compaction),
        )
        print(json.dumps(out, indent=2))
        if not out.get("ok"):
            raise SystemExit(2)
        return True

    return False
