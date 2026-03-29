"""End-to-end demo: write beads, rebuild rolling window, read them back.

This script shows the full memory round-trip through the PydanticAI
integration — no live LLM required. Run it and watch memory flow:

    .venv/bin/python examples/pydanticai_demo_roundtrip.py
"""

import json
import tempfile
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.write_pipeline.rolling_window import build_rolling_surface, write_rolling_surface
from core_memory.integrations.pydanticai import (
    continuity_prompt,
    memory_search_tool,
    memory_reason_tool,
)


def main():
    with tempfile.TemporaryDirectory() as td:
        root = str(Path(td) / "memory")

        # ── Step 1: Seed some beads ──────────────────────────────────
        print("Step 1: Creating beads in the memory store...\n")
        store = MemoryStore(root=root)

        b1 = store.add_bead(
            type="decision",
            title="Chose PostgreSQL",
            summary=["JSONB support for flexible schema", "Strong community and tooling"],
            detail="Evaluated MySQL, SQLite, and PostgreSQL. PostgreSQL won due to native JSONB.",
            session_id="s-demo",
            scope="project",
        )
        print(f"  Created bead: {b1} (decision: Chose PostgreSQL)")

        b2 = store.add_bead(
            type="lesson",
            title="Always benchmark before choosing a database",
            summary=["Ran pgbench and sysbench", "PostgreSQL was 2x faster for our JSON workload"],
            detail="Performance testing prevented us from choosing MySQL which would have been slower.",
            session_id="s-demo",
            scope="project",
        )
        print(f"  Created bead: {b2} (lesson: Always benchmark)")

        b3 = store.add_bead(
            type="goal",
            title="Migrate auth to OAuth2",
            summary=["Legal flagged session token storage", "Deadline: end of Q2"],
            session_id="s-demo",
            scope="project",
        )
        print(f"  Created bead: {b3} (goal: Migrate auth)")

        # ── Step 2: Build the rolling window ─────────────────────────
        print("\nStep 2: Building rolling window (continuity surface)...\n")
        text, meta, included_ids, excluded_ids = build_rolling_surface(root)
        meta["records"] = [
            store._read_json(store.beads_dir / "index.json")["beads"][bid]
            for bid in included_ids
            if bid in store._read_json(store.beads_dir / "index.json")["beads"]
        ]
        write_rolling_surface(root, text, meta, included_ids, excluded_ids)
        print(f"  Rolling window includes {len(included_ids)} bead(s)")

        # ── Step 3: Continuity injection ─────────────────────────────
        print("\nStep 3: Loading continuity prompt (what gets injected into system prompt)...\n")
        prompt = continuity_prompt(root=root)
        print("--- CONTINUITY PROMPT ---")
        print(prompt)
        print("--- END ---")

        # ── Step 4: Memory search tool ───────────────────────────────
        print("\nStep 4: Using memory search tool...\n")
        search = memory_search_tool(root=root)

        result = json.loads(search("PostgreSQL"))
        print(f"  Search 'PostgreSQL': {len(result.get('results', []))} hit(s)")
        for hit in result.get("results", []):
            print(f"    - [{hit.get('type')}] {hit.get('title')}: {hit.get('summary')}")

        result2 = json.loads(search("auth OAuth"))
        print(f"\n  Search 'auth OAuth': {len(result2.get('results', []))} hit(s)")
        for hit in result2.get("results", []):
            print(f"    - [{hit.get('type')}] {hit.get('title')}: {hit.get('summary')}")

        # ── Step 5: Memory reason tool ───────────────────────────────
        print("\nStep 5: Using memory reason tool...\n")
        reason = memory_reason_tool(root=root)
        result3 = json.loads(reason("Why did we choose PostgreSQL?"))
        print(f"  Reason 'Why PostgreSQL?': ok={result3.get('ok')}")
        if result3.get("answer"):
            print(f"  Answer: {result3['answer'][:200]}")

        print("\nDone! Full round-trip: write → rolling window → continuity + search + reason")


if __name__ == "__main__":
    main()
