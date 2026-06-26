from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Any, Protocol, runtime_checkable


T5_JUDGE_PROMPT_VERSION = "t5_answerability_rubric.v1"


@runtime_checkable
class AnswerabilityJudge(Protocol):
    @property
    def kind(self) -> str: ...

    @property
    def is_llm_judge(self) -> bool: ...

    @property
    def prompt_version(self) -> str: ...

    def judge_case(
        self,
        *,
        case: dict[str, Any],
        returned_keys: list[str],
        required_keys: list[str],
        drift_keys: list[str],
        deterministic_metrics: dict[str, Any],
    ) -> dict[str, Any]: ...


class DeterministicAnswerabilityJudge:
    @property
    def kind(self) -> str:
        return "deterministic"

    @property
    def is_llm_judge(self) -> bool:
        return False

    @property
    def prompt_version(self) -> str:
        return T5_JUDGE_PROMPT_VERSION

    def judge_case(
        self,
        *,
        case: dict[str, Any],
        returned_keys: list[str],
        required_keys: list[str],
        drift_keys: list[str],
        deterministic_metrics: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "judge_kind": self.kind,
            "judge_status": "completed",
            "is_llm_judge": False,
            "prompt_version": self.prompt_version,
            "answerability": float(deterministic_metrics.get("answerability") or 0.0),
            "rationale": "deterministic_required_key_and_drift_gate",
        }


class FakeLLMAnswerabilityJudge:
    @property
    def kind(self) -> str:
        return "fake_llm"

    @property
    def is_llm_judge(self) -> bool:
        return True

    @property
    def prompt_version(self) -> str:
        return T5_JUDGE_PROMPT_VERSION

    def judge_case(
        self,
        *,
        case: dict[str, Any],
        returned_keys: list[str],
        required_keys: list[str],
        drift_keys: list[str],
        deterministic_metrics: dict[str, Any],
    ) -> dict[str, Any]:
        required = set(required_keys)
        returned = set(returned_keys)
        drift = set(drift_keys)
        answerability = 1.0 if required.issubset(returned) and not (returned & drift) else 0.0
        return {
            "judge_kind": self.kind,
            "judge_status": "completed",
            "is_llm_judge": True,
            "prompt_version": self.prompt_version,
            "answerability": answerability,
            "rationale": "fake_llm_fixed_rubric_for_offline_tests",
        }


class CommandAnswerabilityJudge:
    def __init__(self, command: str) -> None:
        self.command = command

    @property
    def kind(self) -> str:
        return "llm"

    @property
    def is_llm_judge(self) -> bool:
        return True

    @property
    def prompt_version(self) -> str:
        return T5_JUDGE_PROMPT_VERSION

    def judge_case(
        self,
        *,
        case: dict[str, Any],
        returned_keys: list[str],
        required_keys: list[str],
        drift_keys: list[str],
        deterministic_metrics: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.command.strip():
            return {
                "judge_kind": self.kind,
                "judge_status": "unavailable",
                "is_llm_judge": True,
                "prompt_version": self.prompt_version,
                "answerability": None,
                "rationale": "CORE_MEMORY_T5_LLM_JUDGE_COMMAND_not_configured",
            }

        payload = {
            "prompt_version": self.prompt_version,
            "case_id": str(case.get("id") or ""),
            "query": str(case.get("query") or ""),
            "returned_keys": returned_keys,
            "required_keys": required_keys,
            "drift_keys": drift_keys,
            "deterministic_metrics": deterministic_metrics,
            "rubric": {
                "answerable": "required keys are present and no material drift keys are needed for the answer",
                "not_answerable": "required keys are missing or the answer depends on off-thread drift evidence",
            },
        }
        try:
            completed = subprocess.run(
                shlex.split(self.command),
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except Exception as exc:  # pragma: no cover - defensive external hook
            return {
                "judge_kind": self.kind,
                "judge_status": "failed",
                "is_llm_judge": True,
                "prompt_version": self.prompt_version,
                "answerability": None,
                "rationale": f"{type(exc).__name__}: {exc}",
            }
        if completed.returncode != 0:
            return {
                "judge_kind": self.kind,
                "judge_status": "failed",
                "is_llm_judge": True,
                "prompt_version": self.prompt_version,
                "answerability": None,
                "rationale": (completed.stderr or completed.stdout or "judge_command_failed")[:500],
            }
        try:
            row = json.loads(completed.stdout or "{}")
        except Exception:
            return {
                "judge_kind": self.kind,
                "judge_status": "failed",
                "is_llm_judge": True,
                "prompt_version": self.prompt_version,
                "answerability": None,
                "rationale": "judge_command_returned_non_json",
            }
        answerability = row.get("answerability")
        return {
            "judge_kind": self.kind,
            "judge_status": "completed",
            "is_llm_judge": True,
            "prompt_version": str(row.get("prompt_version") or self.prompt_version),
            "answerability": float(answerability) if answerability is not None else None,
            "rationale": str(row.get("rationale") or "external_fixed_rubric_judge"),
        }


def build_answerability_judge(kind: str | None = None) -> AnswerabilityJudge:
    normalized = str(kind or os.environ.get("CORE_MEMORY_T5_JUDGE_KIND") or "deterministic").strip().lower()
    if normalized in {"deterministic", "proxy", ""}:
        return DeterministicAnswerabilityJudge()
    if normalized in {"fake", "fake_llm", "fake-llm"}:
        return FakeLLMAnswerabilityJudge()
    if normalized == "llm":
        return CommandAnswerabilityJudge(os.environ.get("CORE_MEMORY_T5_LLM_JUDGE_COMMAND", ""))
    raise ValueError(f"unsupported_t5_judge_kind:{normalized}")


__all__ = [
    "AnswerabilityJudge",
    "T5_JUDGE_PROMPT_VERSION",
    "build_answerability_judge",
]
