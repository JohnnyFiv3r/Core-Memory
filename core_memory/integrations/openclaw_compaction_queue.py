from __future__ import annotations

import json
import sys
from typing import Any

from core_memory.integrations.openclaw_compaction_bridge import process_compaction_event
from core_memory.runtime.compaction_queue import (
    enqueue_compaction_event as _enqueue_compaction_event,
    drain_compaction_queue as _drain_compaction_queue,
)


def enqueue_compaction_event(*, event: dict[str, Any], ctx: dict[str, Any] | None = None, root: str | None = None) -> dict[str, Any]:
    """Compatibility wrapper for adapter-local queue enqueue API."""
    return _enqueue_compaction_event(event=event, ctx=ctx, root=root)


def drain_compaction_queue(*, root: str | None = None, max_items: int = 1) -> dict[str, Any]:
    """Compatibility wrapper for adapter-local queue drain API.

    Uses OpenClaw bridge processing policy for backward compatibility while the
    queue storage/flow primitives live in runtime.
    """
    return _drain_compaction_queue(
        root=root,
        max_items=max_items,
        processor=process_compaction_event,
    )


def main() -> None:
    raw = sys.stdin.buffer.read().decode("utf-8", "ignore").strip()
    if not raw:
        print(json.dumps({"ok": False, "error": "missing_input"}))
        return

    payload = json.loads(raw)
    action = str(payload.get("action") or "enqueue")
    root = payload.get("root")

    if action == "enqueue":
        event = payload.get("event") if isinstance(payload.get("event"), dict) else payload
        ctx = payload.get("ctx") if isinstance(payload.get("ctx"), dict) else payload.get("context")
        out = enqueue_compaction_event(event=event, ctx=ctx, root=str(root) if root else None)
        print(json.dumps(out, ensure_ascii=False))
        return

    if action == "drain":
        out = drain_compaction_queue(root=str(root) if root else None, max_items=int(payload.get("maxItems") or 1))
        print(json.dumps(out, ensure_ascii=False))
        return

    print(json.dumps({"ok": False, "error": f"unknown_action:{action}"}))


if __name__ == "__main__":
    main()
