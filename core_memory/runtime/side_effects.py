from __future__ import annotations

import os
from typing import Any

from core_memory.retrieval.lifecycle import enqueue_semantic_rebuild
from core_memory.runtime.side_effect_queue import enqueue_side_effect_event


def _mode() -> str:
    m = str(os.environ.get("CORE_MEMORY_ASYNC_SIDE_EFFECTS_MODE") or "enqueue").strip().lower()
    if m not in {"off", "enqueue"}:
        return "enqueue"
    return m


def _enabled_set() -> set[str]:
    raw = str(
        os.environ.get("CORE_MEMORY_ASYNC_SIDE_EFFECTS")
        or "semantic-rebuild,dreamer-run,neo4j-sync,health-recompute"
    )
    out = set()
    for part in raw.split(","):
        p = part.strip().lower().replace("_", "-")
        if p:
            out.add(p)
    return out


def enqueue_post_write_side_effects(
    *,
    root: str,
    session_id: str,
    flush_tx_id: str,
    source: str,
) -> dict[str, Any]:
    mode = _mode()
    enabled = _enabled_set()
    out: dict[str, Any] = {
        "ok": True,
        "mode": mode,
        "enabled": sorted(enabled),
        "enqueued": {},
    }

    if mode == "off":
        out["skipped"] = True
        return out

    if "semantic-rebuild" in enabled or "semantic" in enabled:
        out["enqueued"]["semantic-rebuild"] = enqueue_semantic_rebuild(root)

    if "dreamer-run" in enabled or "dreamer" in enabled:
        out["enqueued"]["dreamer-run"] = enqueue_side_effect_event(
            root=root,
            kind="dreamer-run",
            payload={
                "session_id": str(session_id),
                "flush_tx_id": str(flush_tx_id),
                "source": str(source),
                "novel_only": True,
                "seen_window_runs": 3,
                "max_exposure": 10,
            },
            idempotency_key=f"dreamer:{session_id}:{flush_tx_id}",
        )

    if "neo4j-sync" in enabled or "neo4j" in enabled:
        out["enqueued"]["neo4j-sync"] = enqueue_side_effect_event(
            root=root,
            kind="neo4j-sync",
            payload={
                "session_id": str(session_id),
                "flush_tx_id": str(flush_tx_id),
                "source": str(source),
            },
            idempotency_key=f"neo4j:{session_id}:{flush_tx_id}",
        )

    if "health-recompute" in enabled or "health" in enabled:
        out["enqueued"]["health-recompute"] = enqueue_side_effect_event(
            root=root,
            kind="health-recompute",
            payload={
                "session_id": str(session_id),
                "flush_tx_id": str(flush_tx_id),
                "source": str(source),
            },
            idempotency_key=f"health:{session_id}:{flush_tx_id}",
        )

    return out


__all__ = ["enqueue_post_write_side_effects"]
