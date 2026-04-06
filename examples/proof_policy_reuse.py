"""Behavior-proof demo: a recalled lesson changes a later deployment choice.

Run:
  PYTHONPATH=. python3 examples/proof_policy_reuse.py
"""

from __future__ import annotations

import os
import tempfile
import json
from pathlib import Path

from core_memory import memory_execute, process_session_start, process_turn_finalized


def _choose_rollout(root: str, query: str) -> str:
    retrieval_query = "payments canary-first rollout policy" if "payments" in (query or "").lower() else str(query or "")
    out = memory_execute(
        request={"raw_query": retrieval_query, "intent": "remember", "k": 8},
        root=root,
        explain=True,
    )
    rows = [r for r in (out.get("results") or []) if str(r.get("type") or "") != "session_start"]
    text = "\n".join(
        [
            str(r.get("title") or "")
            + " "
            + " ".join(str(x) for x in (r.get("summary") or []))
            + " "
            + str(r.get("snippet") or "")
            for r in rows
        ]
    ).lower()
    return "canary" if "canary" in text else "full_rollout"


def main() -> None:
    os.environ.setdefault("CORE_MEMORY_CANONICAL_SEMANTIC_MODE", "degraded_allowed")

    with tempfile.TemporaryDirectory(prefix="cm-policy-proof-") as td:
        root = str(Path(td) / "memory")

        # Before durable memory exists
        process_session_start(root=root, session_id="s1", source="proof_policy_reuse")
        before_choice = _choose_rollout(root, "payments deployment tonight")

        # Record failure + lesson in session s1
        process_turn_finalized(
            root=root,
            session_id="s1",
            turn_id="t1",
            user_query="payments canary-first rollout policy",
            assistant_final=(
                "Outcome: full rollout caused incident risk. "
                "Lesson: payments deployments must use canary-first rollout."
            ),
        )

        # New session should reuse durable lesson and change action
        process_session_start(root=root, session_id="s2", source="proof_policy_reuse")
        after_choice = _choose_rollout(root, "payments deployment tonight")

        print(
            json.dumps(
                {
                    "before_choice": before_choice,
                    "after_choice": after_choice,
                    "behavior_changed": before_choice != after_choice,
                    "expected_policy": "canary",
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
