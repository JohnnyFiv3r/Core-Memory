"""Product semantic-runtime adapter used by Observation Ledger evaluations."""

from __future__ import annotations

from typing import Any

from core_memory.policy.semantic_task_runtime import get_semantic_task_runtime
from core_memory.schema.semantic_tasks import SEMANTIC_TASK_TYPES, SemanticTaskRequest

from .models import ResolvedModel, RuntimeCaseResult
from .pack import runtime_input
from .providers import ModelExecutionError, parse_json_response


class CoreMemorySemanticRuntimeAdapter:
    """Exercise Core Memory's semantic task boundary with the bound author model."""

    name = "core_memory.semantic_task_runtime.v1"
    semantic_roles = tuple(sorted(SEMANTIC_TASK_TYPES))

    def execute(self, case: dict[str, Any], author: ResolvedModel) -> RuntimeCaseResult:
        task_input = dict(case.get("task_input") or {})
        task_type = str(task_input.get("task_type") or "").strip()
        if task_type not in SEMANTIC_TASK_TYPES:
            raise ModelExecutionError(f"unsupported_semantic_task_type:{task_type or 'missing'}")
        prompt = str(task_input.get("prompt") or "").strip()
        if not prompt:
            raise ModelExecutionError("semantic_task_prompt_missing")

        request = SemanticTaskRequest(
            task_type=task_type,
            prompt=prompt,
            payload=runtime_input(case),
            root=None,
            prompt_version=str(task_input.get("prompt_version") or "observation-ledger-runtime.v1"),
            rubric_version=str(task_input.get("rubric_version") or ""),
            output_schema=str(task_input.get("output_schema") or ""),
            model_tier=str(task_input.get("model_tier") or ""),
            max_tokens=max(1, int(task_input.get("max_tokens") or 1200)),
            temperature=0,
            json_mode=True,
            fallback_mode="",
            evidence_refs=[
                str(row.get("evidence_id") or "")
                for row in case.get("admissible_evidence") or []
                if isinstance(row, dict)
            ],
            metadata={},
        )
        result = get_semantic_task_runtime(mode="provider").run(request)
        if not result.ok:
            raise ModelExecutionError(f"author_runtime_failed:{result.status or 'failed'}")
        candidate = result.output_json
        if candidate is None:
            candidate = parse_json_response(result.output_text)

        failures: list[str] = []
        profile_model = result.model_profile.model if result.model_profile else ""
        if profile_model and profile_model != author.model:
            failures.append("author_model_mismatch")
        deterministic_fallback = bool(
            result.fallback_mode or (result.metadata or {}).get("deterministic_fallback_used")
        )
        token_usage = dict(result.token_usage or {})
        return RuntimeCaseResult(
            candidate=dict(candidate),
            semantic_roles_exercised=(task_type,),
            model_identifier=author.identifier,
            latency_ms=float(result.latency_ms or 0),
            input_tokens=int(token_usage.get("input_tokens") or 0),
            output_tokens=int(token_usage.get("output_tokens") or 0),
            cost_usd=float(token_usage.get("cost_usd") or 0.0),
            hard_invariant_failures=tuple(failures),
            deterministic_fallback_used=deterministic_fallback,
        )

    def state_digest(self) -> str:
        # The semantic benchmark request deliberately has no persistence root.
        return "no-engine-state-handle"
