"""PydanticAI-style demo where durable memory changes a later answer.

Contract Level: Experimental
Audience: Adapter experimentation and custom wiring

Demonstrates:
1) baseline answer before memory exists
2) canonical writeback via `run_with_memory(...)`
3) changed answer after retrieval sees durable memory

Run:
  PYTHONPATH=. python3 examples/pydanticai_with_memory.py
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from core_memory.integrations.pydanticai import memory_execute_tool, run_with_memory


class FakeResult:
    def __init__(self, output: str):
        self.output = output


class FakeAgent:
    """Stub agent used only for finalized-turn writeback via run_with_memory."""

    async def run(self, user_query: str) -> FakeResult:
        return FakeResult(output=f"Stub answer: {user_query}")


def _answer_from_execute(payload: dict) -> str:
    rows = list(payload.get("results") or [])
    preferred = [r for r in rows if str(r.get("type") or "") != "session_start"]
    top = (preferred or rows or [{}])[0] or {}
    title = str(top.get("title") or "").strip()
    if not title:
        return "I don't have a recorded durable reason yet."
    return f"Based on memory, reason: {title}"


async def main() -> None:
    os.environ.setdefault("CORE_MEMORY_CANONICAL_SEMANTIC_MODE", "degraded_allowed")

    with tempfile.TemporaryDirectory() as td:
        root = str(Path(td) / "memory")
        session_id = "demo"
        agent = FakeAgent()
        execute_memory = memory_execute_tool(root=root)

        # Before writing any memory
        before_payload = json.loads(execute_memory("why did we choose postgresql", intent="causal"))
        before_answer = _answer_from_execute(before_payload)
        print("Before:", before_answer)

        # Write one durable turn through canonical finalized-turn writeback
        await run_with_memory(
            agent,
            "Decision: choose PostgreSQL for JSONB and transactional consistency.",
            root=root,
            session_id=session_id,
            turn_id="t1",
        )

        # Query again after writeback
        after_payload = json.loads(execute_memory("why did we choose postgresql", intent="causal"))
        after_answer = _answer_from_execute(after_payload)
        print("After:", after_answer)
        print("Behavior changed:", before_answer != after_answer)


if __name__ == "__main__":
    asyncio.run(main())
