"""Canonical Core Memory quickstart (5-minute path).

Contract Level: Canonical
Audience: First-touch adopters who want a fuller walkthrough

Run:
  PYTHONPATH=. python3 examples/quickstart.py

This path intentionally uses canonical write + retrieval boundaries:
- process_turn_finalized(...)
- memory_execute(...)
- memory_trace(...)

By default the script enables degraded semantic mode for frictionless first run
when semantic extras are not installed. To test strict canonical semantic mode,
set CORE_MEMORY_CANONICAL_SEMANTIC_MODE=required before running.
"""

from __future__ import annotations

import os

from core_memory import process_turn_finalized, memory_execute, memory_trace


def main() -> None:
    root = "./memory"

    if not os.getenv("CORE_MEMORY_CANONICAL_SEMANTIC_MODE"):
        os.environ["CORE_MEMORY_CANONICAL_SEMANTIC_MODE"] = "degraded_allowed"

    process_turn_finalized(
        root=root,
        session_id="quickstart",
        turn_id="t1",
        user_query="We saw Redis timeouts under load. What should we do?",
        assistant_final="Decision: increase Redis pool size to 200 to prevent pool exhaustion.",
    )

    process_turn_finalized(
        root=root,
        session_id="quickstart",
        turn_id="t2",
        user_query="Did that change work?",
        assistant_final="Outcome: timeout rate dropped after raising the pool size.",
    )

    out = memory_execute(
        request={"raw_query": "why did we increase redis pool size", "intent": "causal", "k": 5},
        root=root,
        explain=True,
    )

    print(f"execute.ok={out.get('ok')} degraded={out.get('degraded', False)}")
    if not out.get("ok"):
        err = out.get("error") or {}
        print("execute.error:", err)
        print("Hint: install semantic extras with `pip install \"core-memory[semantic]\"`")
        return

    print("Top results:")
    for row in (out.get("results") or [])[:3]:
        print(f"- [{row.get('type')}] {row.get('title')} (score={row.get('score', 0):.3f})")

    tr = memory_trace(query="why redis pool size", root=root, k=5)
    print(f"trace.ok={tr.get('ok')} chains={len(tr.get('chains') or [])}")


if __name__ == "__main__":
    main()
