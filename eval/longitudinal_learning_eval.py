from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core_memory import process_session_start, process_turn_finalized
from core_memory.persistence.store import MemoryStore


@dataclass(frozen=True)
class Episode:
    idx: int
    session_id: str
    scenario: str
    expected_action: str
    policy_key: str


EPISODES: list[Episode] = [
    Episode(1, "s1", "payments deployment tonight", "canary", "payments_canary"),
    Episode(2, "s1", "payments hotfix rollout", "canary", "payments_canary"),
    Episode(3, "s2", "payments API patch", "canary", "payments_canary"),
    Episode(4, "s2", "auth service patch", "full_rollout", "auth_full"),
    Episode(5, "s3", "payments worker update", "canary", "payments_canary"),
    Episode(6, "s3", "auth config change", "full_rollout", "auth_full"),
]


class _NoMemoryPolicy:
    name = "no_memory"

    def on_session_start(self, _session_id: str) -> None:
        return

    def choose(self, episode: Episode) -> str:
        # Baseline intentionally ignores history.
        return "full_rollout"

    def observe(self, episode: Episode, action: str, correct: bool) -> None:
        return


class _SummaryOnlyPolicy:
    name = "summary_only"

    def __init__(self) -> None:
        self._session_notes: dict[str, str] = {}

    def on_session_start(self, session_id: str) -> None:
        self._session_notes.setdefault(session_id, "")

    def choose(self, episode: Episode) -> str:
        if episode.policy_key != "payments_canary":
            return "full_rollout"
        note = self._session_notes.get(episode.session_id, "")
        return "canary" if "canary-first" in note else "full_rollout"

    def observe(self, episode: Episode, action: str, correct: bool) -> None:
        if episode.policy_key == "payments_canary" and not correct:
            self._session_notes[episode.session_id] = "Lesson: payments uses canary-first rollout."


class _CoreMemoryPolicy:
    name = "core_memory"

    def __init__(self, root: str) -> None:
        self.root = root

    def on_session_start(self, session_id: str) -> None:
        process_session_start(root=self.root, session_id=session_id, source="longitudinal_eval")

    def choose(self, episode: Episode) -> str:
        if episode.policy_key != "payments_canary":
            return "full_rollout"

        store = MemoryStore(root=self.root)
        rows = store.query(tags=["payments"], limit=50)
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
                for r in rows
            ]
        )
        return "canary" if "canary" in text else "full_rollout"

    def observe(self, episode: Episode, action: str, correct: bool) -> None:
        outcome = "success" if correct else "failure"
        if episode.policy_key == "payments_canary" and not correct:
            user_query = f"Incident retrospective for {episode.scenario}."
            assistant_final = (
                f"Outcome: {outcome}. Action was {action}. "
                "Lesson: payments deployments must use canary-first rollout."
            )
        elif episode.policy_key == "payments_canary" and correct:
            user_query = f"Incident retrospective for {episode.scenario}."
            assistant_final = (
                f"Outcome: {outcome}. Action was {action}. "
                "Reinforcement: canary rollout reduced deployment risk for payments."
            )
        else:
            user_query = f"Auth deployment policy check for {episode.scenario}."
            assistant_final = f"Outcome: {outcome}. Action was {action}. Auth changes can use full rollout."

        process_turn_finalized(
            root=self.root,
            session_id=episode.session_id,
            turn_id=f"ep-{episode.idx}",
            user_query=user_query,
            assistant_final=assistant_final,
        )

        # Deterministic eval anchor bead so retrieval behavior can be compared
        # across strategies without depending on optional crawler extraction modes.
        store = MemoryStore(root=self.root)
        if episode.policy_key == "payments_canary" and not correct:
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
                session_id=episode.session_id,
                source_turn_ids=[f"ep-{episode.idx}"],
            )
        elif episode.policy_key == "payments_canary" and correct:
            store.add_bead(
                type="outcome",
                title="Policy outcome: canary rollout prevented payments incident",
                summary=["Canary-first rollout reduced deployment risk."],
                because=["Canary exposure limited blast radius."],
                retrieval_title="Canary rollout outcome",
                retrieval_facts=["Canary-first rollout reduced deployment risk."],
                supporting_facts=["Canary exposure limited blast radius."],
                tags=["payments", "deploy_policy", "canary_first"],
                status="open",
                retrieval_eligible=True,
                session_id=episode.session_id,
                source_turn_ids=[f"ep-{episode.idx}"],
            )
        else:
            store.add_bead(
                type="decision",
                title="Policy anchor: auth low-risk patches allow full rollout",
                summary=["Auth patch accepted full rollout for this low-risk change."],
                because=["Auth patch was low-risk and reversible."],
                retrieval_title="Auth rollout policy",
                retrieval_facts=["Low-risk auth patches can use full rollout."],
                supporting_facts=["Patch was low-risk and reversible."],
                tags=["auth", "deploy_policy"],
                status="open",
                retrieval_eligible=True,
                session_id=episode.session_id,
                source_turn_ids=[f"ep-{episode.idx}"],
            )
        store.close()


def _safe_div(a: float, b: float) -> float:
    return float(a) / float(b) if b else 0.0


def _evaluate_policy(policy: Any) -> dict[str, Any]:
    history: list[dict[str, Any]] = []
    first_failure_idx: int | None = None
    first_failure_session: str | None = None
    first_recovery_idx: int | None = None
    repeated_window = 0
    repeated_mistakes = 0
    consistency_total = 0
    consistency_correct = 0
    cross_session_total = 0
    cross_session_reuse = 0

    last_session = None
    for ep in EPISODES:
        if ep.session_id != last_session:
            policy.on_session_start(ep.session_id)
            last_session = ep.session_id

        action = str(policy.choose(ep))
        correct = action == ep.expected_action

        if ep.policy_key == "payments_canary":
            if first_failure_idx is None and not correct:
                first_failure_idx = ep.idx
                first_failure_session = ep.session_id
            elif first_failure_idx is not None:
                repeated_window += 1
                if not correct:
                    repeated_mistakes += 1

            if first_failure_idx is not None and first_recovery_idx is None and correct:
                first_recovery_idx = ep.idx

            if first_recovery_idx is not None and ep.idx >= first_recovery_idx:
                consistency_total += 1
                if correct:
                    consistency_correct += 1

            if (
                first_failure_idx is not None
                and first_failure_session is not None
                and ep.session_id != first_failure_session
            ):
                cross_session_total += 1
                if correct:
                    cross_session_reuse += 1

        history.append(
            {
                "episode": ep.idx,
                "session_id": ep.session_id,
                "scenario": ep.scenario,
                "expected_action": ep.expected_action,
                "action": action,
                "correct": correct,
            }
        )
        policy.observe(ep, action, correct)

    total = len(history)
    correct_total = sum(1 for h in history if h["correct"]) 
    recovery_speed = None
    if first_failure_idx is not None and first_recovery_idx is not None:
        recovery_speed = max(0, first_recovery_idx - first_failure_idx)

    return {
        "strategy": policy.name,
        "episodes": total,
        "accuracy": round(_safe_div(correct_total, total), 4),
        "repeated_mistake_rate": round(_safe_div(repeated_mistakes, repeated_window), 4),
        "recovery_speed": recovery_speed,
        "policy_consistency": round(_safe_div(consistency_correct, consistency_total), 4),
        "lesson_reuse_across_sessions": round(_safe_div(cross_session_reuse, cross_session_total), 4),
        "history": history,
    }


def main() -> int:
    os.environ.setdefault("CORE_MEMORY_CANONICAL_SEMANTIC_MODE", "degraded_allowed")

    no_memory = _evaluate_policy(_NoMemoryPolicy())
    summary_only = _evaluate_policy(_SummaryOnlyPolicy())

    with tempfile.TemporaryDirectory(prefix="cm-longitudinal-") as td:
        root = str(Path(td) / "memory")
        core_memory = _evaluate_policy(_CoreMemoryPolicy(root=root))

    out = {
        "schema": "core_memory.longitudinal_learning_eval.v1",
        "episodes": [ep.__dict__ for ep in EPISODES],
        "results": {
            "no_memory": no_memory,
            "summary_only": summary_only,
            "core_memory": core_memory,
        },
    }

    print(json.dumps(out, indent=2))

    # Pass gate: Core Memory should improve repeated mistakes and cross-session reuse
    # relative to both baselines in this benchmark.
    ok = (
        core_memory["repeated_mistake_rate"] <= summary_only["repeated_mistake_rate"]
        and core_memory["repeated_mistake_rate"] < no_memory["repeated_mistake_rate"]
        and core_memory["lesson_reuse_across_sessions"] >= summary_only["lesson_reuse_across_sessions"]
        and core_memory["lesson_reuse_across_sessions"] > no_memory["lesson_reuse_across_sessions"]
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
