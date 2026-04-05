"""Compatibility quickstart for direct MemoryStore persistence workflows.

Run:
  PYTHONPATH=. python3 examples/store_compat_quickstart.py

Use this when you intentionally want direct store/index operations.
For canonical runtime/retrieval onboarding, prefer examples/quickstart.py.
"""

from core_memory import MemoryStore


def main() -> None:
    memory = MemoryStore(root="./memory")

    b1 = memory.add_bead(
        type="decision",
        title="Use archive-first writes for durability",
        summary=["Index is projection cache, not source of truth"],
        session_id="quickstart",
    )
    b2 = memory.add_bead(
        type="outcome",
        title="Rebuild safety improved after crash",
        summary=["Session JSONL replay can restore index state"],
        session_id="quickstart",
    )

    results = memory.query(session_id="quickstart", limit=5)
    print(f"Created: {b1}, {b2}")
    print("Recent beads:")
    for row in results[:5]:
        print(f"- [{row.get('type')}] {row.get('title')}")


if __name__ == "__main__":
    main()
