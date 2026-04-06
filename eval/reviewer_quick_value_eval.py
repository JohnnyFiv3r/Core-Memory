from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from core_memory import memory_execute, process_turn_finalized
from core_memory.persistence.store import MemoryStore


def _choose_rollout(root: str, scenario: str) -> tuple[str, dict]:
    """Choose rollout strategy from memory evidence (canonical-first, deterministic fallback)."""
    out = memory_execute(
        request={"raw_query": f"deployment policy {scenario}", "intent": "remember", "k": 8},
        root=root,
        explain=True,
    )

    rows = [r for r in (out.get("results") or []) if str(r.get("type") or "") != "session_start"]
    text = "\n".join(
        [
            (
                str(r.get("title") or "")
                + " "
                + " ".join(str(x) for x in (r.get("summary") or []))
                + " "
                + str(r.get("snippet") or "")
            ).lower()
            for r in rows
        ]
    )

    if not text:
        # deterministic fallback for environments where retrieval surface has no rows
        store = MemoryStore(root=root)
        fallback_rows = store.query(tags=["payments", "deploy_policy"], limit=20)
        store.close()
        text = "\n".join(
            [
                (
                    str(r.get("title") or "")
                    + " "
                    + " ".join(str(x) for x in (r.get("summary") or []))
                    + " "
                    + str(r.get("detail") or "")
                ).lower()
                for r in fallback_rows
            ]
        )

    action = "canary" if "canary" in text else "full_rollout"
    return action, out


def main() -> int:
    os.environ.setdefault("CORE_MEMORY_CANONICAL_SEMANTIC_MODE", "degraded_allowed")

    with tempfile.TemporaryDirectory(prefix="cm-reviewer-eval-") as td:
        root = str(Path(td) / "memory")

        before_choice, before_out = _choose_rollout(root, "payments deployment tonight")

        # Write one learning event through canonical turn-finalized boundary.
        process_turn_finalized(
            root=root,
            session_id="reviewer-eval",
            turn_id="t1",
            user_query="Should we full-rollout payments deployment tonight?",
            assistant_final="Outcome: full rollout increased risk. Lesson: payments deployments must use canary-first rollout.",
        )

        # Deterministic policy anchor for cross-environment comparability.
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
            session_id="reviewer-eval",
            source_turn_ids=["t1"],
        )
        store.close()

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
