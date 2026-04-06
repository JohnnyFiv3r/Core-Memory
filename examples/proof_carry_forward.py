"""Proof-first demo: memory changes future behavior across turns/sessions.

Run:
  PYTHONPATH=. python3 examples/proof_carry_forward.py
"""

from __future__ import annotations

import json
import os
import tempfile
import json
from pathlib import Path

from core_memory import process_turn_finalized, process_session_start, memory_execute


def _count_results(payload: dict) -> int:
    return len(payload.get("results") or [])


def _answer_with_memory(payload: dict) -> str:
    """Tiny policy shim to demonstrate behavior change from retrieval evidence."""
    rows = list(payload.get("results") or [])
    preferred = [r for r in rows if str(r.get("type") or "") != "session_start"]
    top = ((preferred or rows or [{}])[0] or {})
    title = str(top.get("title") or "")
    if not title:
        return "I don't have a recorded durable reason yet."
    return f"Based on memory, the reason was: {title}"


def main() -> None:
    os.environ.setdefault("CORE_MEMORY_CANONICAL_SEMANTIC_MODE", "degraded_allowed")

    with tempfile.TemporaryDirectory(prefix="cm-proof-") as td:
        root = str(Path(td) / "memory")

        # Before writing memory
        before = memory_execute(
            request={"raw_query": "why did we choose postgresql", "intent": "causal", "k": 5},
            root=root,
            explain=True,
        )
        before_answer = _answer_with_memory(before)

        # Write durable memory through canonical finalized-turn boundary
        process_turn_finalized(
            root=root,
            session_id="demo-a",
            turn_id="t1",
            user_query="What database should we use?",
            assistant_final="Decision: choose PostgreSQL for JSONB and transactional consistency.",
        )
        process_turn_finalized(
            root=root,
            session_id="demo-a",
            turn_id="t2",
            user_query="How did it go?",
            assistant_final="Outcome: query reliability improved and schema changes stayed manageable.",
        )

        # New session starts; retrieval should carry forward prior durable memory.
        process_session_start(root=root, session_id="demo-b", source="proof_carry_forward")
        after = memory_execute(
            request={"raw_query": "why did we choose postgresql", "intent": "causal", "k": 5},
            root=root,
            explain=True,
        )
        after_answer = _answer_with_memory(after)

        print(
            json.dumps(
                {
                    "before_result_count": _count_results(before),
                    "after_result_count": _count_results(after),
                    "before_answer": before_answer,
                    "after_answer": after_answer,
                    "behavior_changed": before_answer != after_answer,
                    "after_degraded": after.get("degraded", False),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
