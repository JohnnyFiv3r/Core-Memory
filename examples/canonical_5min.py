"""Smallest believable product path (no adapters, no MemoryStore).

Contract Level: Canonical
Audience: First-touch adopters

Run:
  PYTHONPATH=. python3 examples/canonical_5min.py
"""

from __future__ import annotations

import os

from core_memory import process_turn_finalized, memory_execute


if __name__ == "__main__":
    root = "./memory"
    os.environ.setdefault("CORE_MEMORY_CANONICAL_SEMANTIC_MODE", "degraded_allowed")

    process_turn_finalized(
        root=root,
        session_id="five-minute",
        turn_id="t1",
        user_query="What should we do about Redis timeouts?",
        assistant_final="Decision: increase pool size to 200.",
    )

    out = memory_execute(
        request={"raw_query": "why redis timeouts", "intent": "causal", "k": 5},
        root=root,
        explain=True,
    )

    print({
        "ok": out.get("ok"),
        "degraded": out.get("degraded", False),
        "result_count": len(out.get("results") or []),
        "confidence": out.get("confidence"),
    })
