"""Quick reviewer value eval.

Contract Level: Recommended
Audience: First-pass reviewers validating product value quickly
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from core_memory import memory_execute, process_turn_finalized


def _choose_rollout(root: str, scenario: str) -> tuple[str, dict]:
    retrieval_query = "payments canary-first rollout policy" if "payments" in scenario.lower() else f"deployment policy {scenario}"
    out = memory_execute(
        request={"raw_query": retrieval_query, "intent": "remember", "k": 8},
        root=root,
        explain=True,
    )
    rows = [r for r in (out.get("results") or []) if str(r.get("type") or "") != "session_start"]
    text = "\n".join(
        (
            str(r.get("title") or "")
            + " "
            + " ".join(str(x) for x in (r.get("summary") or []))
            + " "
            + str(r.get("snippet") or "")
        ).lower()
        for r in rows
    )
    action = "canary" if "canary" in text else "full_rollout"
    return action, out


def main() -> int:
    os.environ.setdefault("CORE_MEMORY_CANONICAL_SEMANTIC_MODE", "degraded_allowed")

    with tempfile.TemporaryDirectory(prefix="cm-reviewer-eval-") as td:
        root = str(Path(td) / "memory")

        before_choice, before_out = _choose_rollout(root, "payments deployment tonight")

        process_turn_finalized(
            root=root,
            session_id="reviewer-eval",
            turn_id="t1",
            user_query="payments canary-first rollout policy",
            assistant_final="Outcome: full rollout increased risk. Lesson: payments deployments must use canary-first rollout.",
        )

        after_choice, after_out = _choose_rollout(root, "payments deployment tonight")

        payload = {
            "schema": "core_memory.reviewer_quick_value_eval.v1",
            "before_choice": before_choice,
            "after_choice": after_choice,
            "behavior_changed": before_choice != after_choice,
            "expected_after_choice": "canary",
            "before_result_count": len(before_out.get("results") or []),
            "after_result_count": len(after_out.get("results") or []),
            "after_degraded": bool(after_out.get("degraded", False)),
        }
        print(json.dumps(payload, indent=2))

        return 0 if payload["behavior_changed"] and payload["after_choice"] == "canary" else 2


if __name__ == "__main__":
    raise SystemExit(main())
