from __future__ import annotations

import json
import os
import uuid
from typing import Any

from core_memory.runtime.engine import process_flush
from core_memory.persistence.store import DEFAULT_ROOT


def process_compaction_event(*, event: dict[str, Any], ctx: dict[str, Any] | None = None, root: str | None = None) -> dict[str, Any]:
    ctx = dict(ctx or {})
    root_final = str(root or os.environ.get("CORE_MEMORY_ROOT") or DEFAULT_ROOT)

    session_id = str(
        (ctx or {}).get("sessionId")
        or (ctx or {}).get("sessionKey")
        or event.get("sessionId")
        or event.get("sessionKey")
        or "main"
    )

    compaction = event.get("compaction") if isinstance(event.get("compaction"), dict) else {}
    token_budget = int(compaction.get("tokenBudget") or event.get("tokenBudget") or 1200)
    max_beads = int(compaction.get("maxBeads") or event.get("maxBeads") or 12)
    promote = bool(compaction.get("promote", True))

    flush_tx_id = str(event.get("runId") or event.get("id") or f"flush-{uuid.uuid4().hex[:10]}")

    try:
        out = process_flush(
            root=root_final,
            session_id=session_id,
            promote=promote,
            token_budget=token_budget,
            max_beads=max_beads,
            source="openclaw_compaction_hook",
            flush_tx_id=flush_tx_id,
        )
        return {
            "ok": bool(out.get("ok")),
            "session_id": session_id,
            "flush_tx_id": flush_tx_id,
            "result": out,
        }
    except Exception as exc:
        return {
            "ok": False,
            "session_id": session_id,
            "flush_tx_id": flush_tx_id,
            "error": str(exc),
        }


def main() -> None:
    raw = os.read(0, 10_000_000).decode("utf-8", "ignore").strip()
    if not raw:
        print(json.dumps({"ok": False, "error": "missing_input"}))
        return

    payload = json.loads(raw)
    if not isinstance(payload, dict):
        print(json.dumps({"ok": False, "error": "invalid_input"}))
        return

    event = payload.get("event") if isinstance(payload.get("event"), dict) else payload
    ctx = payload.get("ctx") if isinstance(payload.get("ctx"), dict) else payload.get("context")
    root = payload.get("root")

    out = process_compaction_event(event=event, ctx=ctx, root=str(root) if root else None)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
