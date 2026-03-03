"""Basic PydanticAI integration example (stub-friendly).

This example avoids live model requirements by using a fake agent object.
"""

import asyncio

from core_memory.integrations.pydanticai import run_with_memory


class FakeResult:
    def __init__(self, output: str):
        self.output = output


class FakeAgent:
    async def run(self, user_query: str):
        return FakeResult(output=f"Echo: {user_query}")


async def main():
    agent = FakeAgent()
    result = await run_with_memory(
        agent,
        "hello from pydanticai",
        root="./memory",
        session_id="example-session",
    )
    print(result.output)


if __name__ == "__main__":
    asyncio.run(main())
