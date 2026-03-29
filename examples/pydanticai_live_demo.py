"""Live PydanticAI + Core Memory demo with real LLM calls.

This demonstrates the full memory loop with an actual model:
  1. Seed beads into the memory store
  2. Rebuild the rolling window
  3. Run a PydanticAI agent that gets continuity injection + memory tools
  4. Watch the agent use memory to answer questions
  5. Turn finalization writes the interaction back to memory

Setup:
    pip install -e ".[pydanticai]"

    # Pick ONE provider:
    export ANTHROPIC_API_KEY="sk-ant-..."    # for Claude
    export OPENAI_API_KEY="sk-..."           # for GPT

Usage:
    python examples/pydanticai_live_demo.py                          # auto-detect provider
    python examples/pydanticai_live_demo.py --model anthropic:claude-sonnet-4-20250514
    python examples/pydanticai_live_demo.py --model openai:gpt-4o
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

# Core Memory
from core_memory.persistence.store import MemoryStore
from core_memory.write_pipeline.rolling_window import build_rolling_surface, write_rolling_surface
from core_memory.integrations.pydanticai import (
    continuity_prompt,
    memory_search_tool,
    memory_reason_tool,
    run_with_memory,
)


def _detect_model() -> str:
    """Auto-detect available provider from environment."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic:claude-sonnet-4-20250514"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai:gpt-4o"
    return ""


def _seed_memory(root: str) -> list[str]:
    """Create sample beads that give the agent something to remember."""
    store = MemoryStore(root=root)
    ids = []

    ids.append(store.add_bead(
        type="decision",
        title="Chose PostgreSQL over MySQL",
        summary=[
            "JSONB support enables flexible schema without sacrificing query performance",
            "Mature extension ecosystem (PostGIS, pg_trgm, pgvector)",
            "Strong community backing and long-term stability",
        ],
        detail=(
            "Evaluated MySQL 8, SQLite, and PostgreSQL 16. Ran pgbench and sysbench "
            "against a representative workload. PostgreSQL was 2.1x faster for our "
            "mixed JSON + relational queries. MySQL JSONB equivalent (JSON type) lacks "
            "GIN indexing, which is critical for our search patterns."
        ),
        session_id="s-bootstrap",
        scope="project",
    ))

    ids.append(store.add_bead(
        type="lesson",
        title="Always benchmark before choosing infrastructure",
        summary=[
            "Synthetic benchmarks misled us in a previous project",
            "Representative workload testing caught a 2x performance gap",
        ],
        detail=(
            "In a prior project we chose MySQL based on TPC-C benchmarks, but our "
            "actual workload was JSON-heavy. This time we built a representative "
            "benchmark first, which revealed PostgreSQL's advantage."
        ),
        session_id="s-bootstrap",
        scope="project",
    ))

    ids.append(store.add_bead(
        type="goal",
        title="Migrate authentication to OAuth2",
        summary=[
            "Legal flagged current session-token storage as non-compliant",
            "Target: end of Q2 2026",
            "Must support Google and GitHub as identity providers",
        ],
        session_id="s-bootstrap",
        scope="project",
    ))

    ids.append(store.add_bead(
        type="decision",
        title="Adopted FastAPI for the HTTP layer",
        summary=[
            "Async-first aligns with our I/O-bound workload",
            "OpenAPI spec generation is automatic",
            "PydanticAI integration is native",
        ],
        detail=(
            "Considered Flask, Django REST, and FastAPI. Flask lacks async. Django "
            "is heavyweight for our API-only service. FastAPI gives us automatic "
            "OpenAPI docs, native Pydantic validation, and async without boilerplate."
        ),
        session_id="s-bootstrap",
        scope="project",
    ))

    return ids


def _rebuild_rolling_window(root: str) -> int:
    """Rebuild the rolling window so continuity injection has data."""
    store = MemoryStore(root=root)
    text, meta, included_ids, excluded_ids = build_rolling_surface(root)
    index = store._read_json(store.beads_dir / "index.json")
    beads_map = index.get("beads") or {}
    meta["records"] = [beads_map[bid] for bid in included_ids if bid in beads_map]
    write_rolling_surface(root, text, meta, included_ids, excluded_ids)
    return len(included_ids)


async def run_demo(model_id: str, root: str):
    try:
        from pydantic_ai import Agent
    except ImportError:
        print("Error: pydantic-ai is not installed.")
        print("Run: pip install -e '.[pydanticai]'")
        sys.exit(1)

    # ── Step 1: Seed memory ──────────────────────────────────────
    print("=" * 60)
    print("STEP 1: Seeding memory store with project history")
    print("=" * 60)
    bead_ids = _seed_memory(root)
    print(f"  Created {len(bead_ids)} beads\n")

    # ── Step 2: Build rolling window ─────────────────────────────
    print("=" * 60)
    print("STEP 2: Building rolling window (continuity surface)")
    print("=" * 60)
    n = _rebuild_rolling_window(root)
    print(f"  Rolling window contains {n} bead(s)\n")

    # ── Step 3: Show what the agent sees ─────────────────────────
    print("=" * 60)
    print("STEP 3: Continuity prompt (injected into system prompt)")
    print("=" * 60)
    prompt = continuity_prompt(root=root)
    print(prompt)
    print()

    # ── Step 4: Create agent with memory ─────────────────────────
    print("=" * 60)
    print(f"STEP 4: Creating PydanticAI agent ({model_id})")
    print("=" * 60)

    agent = Agent(
        model_id,
        system_prompt=(
            "You are a project assistant with access to the team's memory. "
            "Use your memory tools to find relevant context before answering. "
            "Be specific — cite decisions, lessons, and goals from memory."
        ),
        tools=[
            memory_search_tool(root=root),
            memory_reason_tool(root=root),
        ],
    )

    @agent.system_prompt
    def inject_memory():
        return continuity_prompt(root=root)

    print("  Agent ready with continuity injection + 2 memory tools\n")

    # ── Step 5: Run conversations ────────────────────────────────
    queries = [
        "Why did we choose PostgreSQL? What were the alternatives?",
        "What's our most urgent deadline right now?",
        "If we need to add vector search, which database decision is relevant?",
    ]

    for i, query in enumerate(queries, 1):
        print("=" * 60)
        print(f"TURN {i}: {query}")
        print("=" * 60)

        result = await run_with_memory(
            agent,
            query,
            root=root,
            session_id="s-live-demo",
            turn_id=f"t-{i}",
        )

        print(f"\nAgent: {result.output}\n")

    print("=" * 60)
    print("DEMO COMPLETE")
    print("=" * 60)
    print(f"Memory store: {root}")
    print(f"Beads created: {len(bead_ids)} (seeded) + turns written back")


def main():
    parser = argparse.ArgumentParser(description="Live PydanticAI + Core Memory demo")
    parser.add_argument(
        "--model",
        default=None,
        help="Model ID (e.g. anthropic:claude-sonnet-4-20250514, openai:gpt-4o). Auto-detects from env if omitted.",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Memory store path. Uses a temp directory if omitted.",
    )
    args = parser.parse_args()

    model_id = args.model or _detect_model()
    if not model_id:
        print("No model detected. Set one of:")
        print("  export ANTHROPIC_API_KEY='sk-ant-...'")
        print("  export OPENAI_API_KEY='sk-...'")
        print("Or pass --model explicitly.")
        sys.exit(1)

    if args.root:
        Path(args.root).mkdir(parents=True, exist_ok=True)
        asyncio.run(run_demo(model_id, args.root))
    else:
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            asyncio.run(run_demo(model_id, root))


if __name__ == "__main__":
    main()
