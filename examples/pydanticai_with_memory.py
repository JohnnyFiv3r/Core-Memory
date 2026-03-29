"""PydanticAI integration example with full read+write memory loop.

Demonstrates:
  1. Continuity injection — rolling-window context as a dynamic system prompt
  2. Memory tools — search and reason available to the agent mid-conversation
  3. Write-back — turn finalization after each agent run

This example uses stub objects so it runs without a live LLM.
Replace FakeAgent with a real pydantic_ai.Agent to use with a model.

Real-world wiring (with pydantic_ai installed):

    from pydantic_ai import Agent
    from core_memory.integrations.pydanticai import (
        continuity_prompt,
        memory_search_tool,
        memory_reason_tool,
        run_with_memory,
    )

    MEMORY_ROOT = "./memory"

    agent = Agent(
        "openai:gpt-4o",
        tools=[
            memory_search_tool(root=MEMORY_ROOT),
            memory_reason_tool(root=MEMORY_ROOT),
        ],
    )

    @agent.system_prompt
    def inject_memory():
        return continuity_prompt(root=MEMORY_ROOT)

    result = await run_with_memory(
        agent, "Why did we choose PostgreSQL?",
        root=MEMORY_ROOT, session_id="sess-001",
    )
"""

import asyncio
import tempfile
from pathlib import Path

from core_memory.integrations.pydanticai import (
    continuity_prompt,
    memory_search_tool,
    memory_reason_tool,
    run_with_memory,
)


class FakeResult:
    def __init__(self, output: str):
        self.output = output


class FakeAgent:
    """Stub agent that simulates a PydanticAI agent with memory awareness."""

    def __init__(self, root: str):
        self._root = root
        self._search = memory_search_tool(root=root)
        self._reason = memory_reason_tool(root=root)

    async def run(self, user_query: str) -> FakeResult:
        # Step 1: Load continuity context (what a real @agent.system_prompt would do)
        context = continuity_prompt(root=self._root)

        # Step 2: Simulate the agent using memory tools
        search_result = self._search(user_query)
        reason_result = self._reason(user_query)

        # Step 3: Compose a response (a real LLM would do this)
        parts = [f"Query: {user_query}"]
        if context:
            parts.append(f"Memory context loaded ({len(context)} chars)")
        else:
            parts.append("No prior memory context")
        parts.append(f"Search: {search_result[:100]}")
        parts.append(f"Reasoning: {reason_result[:100]}")

        return FakeResult(output=" | ".join(parts))


async def main():
    with tempfile.TemporaryDirectory() as td:
        root = str(Path(td) / "memory")
        agent = FakeAgent(root=root)

        # Turn 1: First interaction — no memory yet
        print("── Turn 1 ──")
        r1 = await run_with_memory(
            agent, "We chose PostgreSQL for its JSONB support.",
            root=root, session_id="demo", turn_id="t1",
        )
        print(r1.output)

        # Turn 2: Second interaction — memory from turn 1 may be available
        print("\n── Turn 2 ──")
        r2 = await run_with_memory(
            agent, "Why did we choose PostgreSQL?",
            root=root, session_id="demo", turn_id="t2",
        )
        print(r2.output)

        print("\nDone. Memory stored at:", root)


if __name__ == "__main__":
    asyncio.run(main())
