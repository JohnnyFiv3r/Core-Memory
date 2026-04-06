"""Behavior-proof demo: a recalled lesson changes a later deployment choice.

Run:
  PYTHONPATH=. python3 examples/proof_policy_reuse.py
"""

from __future__ import annotations

import os
import tempfile
import json
from pathlib import Path

from core_memory import process_session_start, process_turn_finalized
from core_memory.persistence.store import MemoryStore


def _choose_rollout(root: str, query: str) -> str:
    store = MemoryStore(root=root)
    rows = store.query(tags=["payments", "deploy_policy"], limit=30)
    store.close()
    text = "\n".join(
        [
            str(r.get("title") or "")
            + " "
            + " ".join(str(x) for x in (r.get("summary") or []))
            + " "
            + str(r.get("detail") or "")
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
            user_query="Should we full-rollout payments deployment?",
            assistant_final=(
                "Outcome: full rollout caused incident risk. "
                "Lesson: payments deployments must use canary-first rollout."
            ),
        )
        store = MemoryStore(root=root)
        store.add_bead(
            type="lesson",
            title="Policy anchor: payments require canary-first rollout",
            summary=["Payments deployments must use canary-first rollout."],
            because=["Full rollout increased incident risk."],
            retrieval_title="Payments rollout policy",
            retrieval_facts=["Payments deployments must use canary-first rollout."],
            supporting_facts=["Full rollout increased incident risk."],
            tags=["payments", "deploy_policy", "canary_first"],
            status="open",
            retrieval_eligible=True,
            session_id="s1",
            source_turn_ids=["t1"],
        )
        store.close()

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
