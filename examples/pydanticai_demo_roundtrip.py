"""PydanticAI roundtrip demo using canonical runtime/retrieval surfaces.

Contract Level: Recommended
Audience: PydanticAI adapter adopters

This script demonstrates value without direct MemoryStore orchestration:
1) finalized-turn writeback via `run_with_memory(...)`
2) continuity read via `continuity_prompt(...)`
3) retrieval via canonical tools (`memory_execute/search/trace`)

Run:
  PYTHONPATH=. python3 examples/pydanticai_demo_roundtrip.py
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from core_memory.integrations.pydanticai import (
    continuity_prompt,
    memory_execute_tool,
    memory_search_tool,
    memory_trace_tool,
    run_with_memory,
)


class FakeResult:
    def __init__(self, output: str):
        self.output = output


class FakeAgent:
    """Stub agent that simulates model output while memory is managed by run_with_memory."""

    async def run(self, user_query: str) -> FakeResult:
        return FakeResult(output=f"Stub answer: {user_query}")


async def main() -> None:
    os.environ.setdefault("CORE_MEMORY_CANONICAL_SEMANTIC_MODE", "degraded_allowed")

    with tempfile.TemporaryDirectory() as td:
        root = str(Path(td) / "memory")
        agent = FakeAgent()

        print("Step 1: finalized-turn writeback via run_with_memory")
        turn1 = await run_with_memory(
            agent,
            "We chose PostgreSQL for JSONB support and transactional consistency.",
            root=root,
            session_id="pyd-demo",
            turn_id="t1",
        )
        print("  turn1:", turn1.output)

        turn2 = await run_with_memory(
            agent,
            "Outcome: reliability improved after the DB decision.",
            root=root,
            session_id="pyd-demo",
            turn_id="t2",
        )
        print("  turn2:", turn2.output)

        print("\nStep 2: continuity injection preview")
        prompt = continuity_prompt(root=root, session_id="pyd-demo")
        preview = (prompt[:240] + "...") if len(prompt) > 240 else prompt
        print("  continuity:", preview or "<empty>")

        print("\nStep 3: canonical retrieval tools")
        execute_memory = memory_execute_tool(root=root)
        search_memory = memory_search_tool(root=root)
        trace_memory = memory_trace_tool(root=root)

        execute_out = json.loads(execute_memory("why did we choose postgresql", intent="causal"))
        print("  execute.ok:", execute_out.get("ok"), "results:", len(execute_out.get("results") or []))
        if execute_out.get("ok") is not True:
            print("  execute.error:", execute_out.get("error"))

        search_out = json.loads(search_memory("postgresql jsonb", k=5))
        print("  search.results:", len(search_out.get("results") or []))

        trace_out = json.loads(trace_memory("why did we choose postgresql", k=5))
        print("  trace.ok:", trace_out.get("ok"), "chains:", len(trace_out.get("chains") or []))


if __name__ == "__main__":
    asyncio.run(main())
