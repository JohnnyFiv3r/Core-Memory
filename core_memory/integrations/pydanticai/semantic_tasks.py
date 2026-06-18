from __future__ import annotations

"""Optional PydanticAI semantic task runtime adapter.

This module intentionally lives under ``integrations/pydanticai`` so the core
semantic task contracts remain dependency-light. Importing this integration may
load ``pydantic_ai``; importing ``core_memory`` or ``core_memory.runtime`` must
not.
"""

import json
import time
import uuid
from typing import Any

from core_memory.runtime.semantic_tasks.contracts import SemanticTaskRequest, SemanticTaskResult
from core_memory.runtime.semantic_tasks.receipts import record_semantic_task_run, stable_hash
from core_memory.runtime.semantic_tasks.runtime import resolve_model_profile


def _agent_class() -> Any:
    from pydantic_ai import Agent  # type: ignore

    return Agent


def _task_id(request: SemanticTaskRequest) -> str:
    if request.task_id:
        return str(request.task_id)
    basis = request.idempotency_key or stable_hash(
        {"task_type": request.task_type, "prompt": request.prompt, "payload": request.payload}
    )
    return f"semtask-{basis}-{uuid.uuid4().hex[:8]}"


def _parse_json(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.strip().startswith("json"):
            raw = raw.strip()[4:]
    try:
        obj = json.loads(raw.strip())
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _output_text(result: Any) -> str:
    for attr in ("output", "data", "text"):
        if hasattr(result, attr):
            value = getattr(result, attr)
            if value is not None:
                return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return str(result or "")


def _usage_dict(result: Any) -> dict[str, Any]:
    usage = None
    if hasattr(result, "usage") and callable(getattr(result, "usage")):
        try:
            usage = result.usage()
        except Exception:
            usage = None
    if usage is None:
        usage = getattr(result, "usage", None)
    if usage is None:
        return {}
    if hasattr(usage, "__dict__"):
        return {k: v for k, v in vars(usage).items() if v not in (None, "", [], {})}
    if isinstance(usage, dict):
        return {k: v for k, v in usage.items() if v not in (None, "", [], {})}
    return {}


def _request_prompt(request: SemanticTaskRequest) -> str:
    if request.prompt:
        return str(request.prompt)
    return json.dumps(request.payload or {}, ensure_ascii=False, sort_keys=True)


def _receipted(request: SemanticTaskRequest, result: SemanticTaskResult) -> SemanticTaskResult:
    if not request.root:
        return result
    row = record_semantic_task_run(request.root, request, result)
    return SemanticTaskResult(
        **{
            **result.as_dict(),
            "model_profile": result.model_profile,
            "receipt_id": str(row.get("receipt_id") or ""),
        }
    )


def _unavailable_result(
    request: SemanticTaskRequest,
    *,
    task_id: str,
    error: str,
    metadata: dict[str, Any] | None = None,
) -> SemanticTaskResult:
    profile = resolve_model_profile(request.task_type, runtime="pydanticai", model_tier=request.model_tier)
    result = SemanticTaskResult(
        task_id=task_id,
        task_type=request.task_type,
        ok=False,
        status="unavailable",
        model_profile=profile,
        prompt_version=request.prompt_version,
        rubric_version=request.rubric_version,
        output_schema=request.output_schema,
        input_hash=stable_hash({"task_type": request.task_type, "prompt": request.prompt, "payload": request.payload}),
        output_hash=stable_hash({"error": error}),
        fallback_mode=request.fallback_mode,
        authority_boundary=request.authority_boundary,
        evidence_refs=list(request.evidence_refs or []),
        error=error,
        metadata={"runtime_mode": "pydanticai", **dict(request.metadata or {}), **dict(metadata or {})},
    )
    return _receipted(request, result)


class PydanticAISemanticTaskRuntime:
    def run(self, request: SemanticTaskRequest) -> SemanticTaskResult:
        task_id = _task_id(request)
        try:
            Agent = _agent_class()
        except Exception:
            return _unavailable_result(
                request,
                task_id=task_id,
                error="pydanticai_semantic_task_runtime_unavailable",
            )

        profile = resolve_model_profile(request.task_type, runtime="pydanticai", model_tier=request.model_tier)
        if not profile.model:
            return _unavailable_result(request, task_id=task_id, error="missing_pydanticai_model")

        prompt = _request_prompt(request)
        model_settings = {"temperature": float(request.temperature or 0)}
        if request.max_tokens:
            model_settings["max_tokens"] = max(1, int(request.max_tokens))

        started = time.perf_counter()
        try:
            agent = Agent(profile.model, output_type=str, instructions="")
            try:
                run_result = agent.run_sync(prompt, model_settings=model_settings)
            except TypeError:
                # Older/newer PydanticAI releases may not accept dict model
                # settings uniformly. Preserve execution over settings fidelity.
                run_result = agent.run_sync(prompt)
            text = _output_text(run_result)
            parsed = _parse_json(text) if request.json_mode else None
            latency_ms = int((time.perf_counter() - started) * 1000)
            result = SemanticTaskResult(
                task_id=task_id,
                task_type=request.task_type,
                ok=True,
                status="succeeded",
                output_text=text,
                output_json=parsed,
                model_profile=profile,
                prompt_version=request.prompt_version,
                rubric_version=request.rubric_version,
                output_schema=request.output_schema,
                input_hash=stable_hash({"task_type": request.task_type, "prompt": prompt, "payload": request.payload}),
                output_hash=stable_hash({"output_text": text, "output_json": parsed}),
                latency_ms=latency_ms,
                token_usage=_usage_dict(run_result),
                fallback_mode=request.fallback_mode,
                authority_boundary=request.authority_boundary,
                evidence_refs=list(request.evidence_refs or []),
                metadata={"runtime_mode": "pydanticai", **dict(request.metadata or {})},
            )
            return _receipted(request, result)
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.perf_counter() - started) * 1000)
            result = SemanticTaskResult(
                task_id=task_id,
                task_type=request.task_type,
                ok=False,
                status="failed",
                model_profile=profile,
                prompt_version=request.prompt_version,
                rubric_version=request.rubric_version,
                output_schema=request.output_schema,
                input_hash=stable_hash({"task_type": request.task_type, "prompt": prompt, "payload": request.payload}),
                output_hash=stable_hash({"error": str(exc)}),
                latency_ms=latency_ms,
                fallback_mode=request.fallback_mode,
                authority_boundary=request.authority_boundary,
                evidence_refs=list(request.evidence_refs or []),
                error=str(exc),
                metadata={"runtime_mode": "pydanticai", **dict(request.metadata or {})},
            )
            return _receipted(request, result)


__all__ = ["PydanticAISemanticTaskRuntime"]
