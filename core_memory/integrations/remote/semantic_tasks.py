"""HTTP-delegated semantic task runtime.

This adapter keeps Core Memory engine semantics local while delegating the LLM
judgment call to an opaque HTTP endpoint such as the Satorid conductor.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import asdict, replace
from typing import Any

from core_memory.runtime.semantic_tasks.contracts import (
    DEFAULT_TASK_MODEL_TIERS,
    MODEL_TIER_STANDARD,
    MODEL_TIERS,
    ModelProfile,
    SemanticTaskRequest,
    SemanticTaskResult,
    SemanticTaskRuntime,
)
from core_memory.runtime.semantic_tasks.receipts import (
    record_semantic_task_run,
    stable_hash,
)


class RemoteSemanticTaskRuntime:
    def __init__(
        self,
        *,
        url: str | None = None,
        token: str | None = None,
        timeout_seconds: float | None = None,
        strict: bool | None = None,
        fallback_runtime: SemanticTaskRuntime | None = None,
    ) -> None:
        self.url = (url or os.environ.get("CORE_MEMORY_SEMANTIC_TASK_RUNTIME_URL") or "").strip()
        self.token = (
            token
            or os.environ.get("CORE_MEMORY_SEMANTIC_TASK_RUNTIME_TOKEN")
            or ""
        ).strip()
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else _float_env("CORE_MEMORY_SEMANTIC_TASK_RUNTIME_TIMEOUT_SECONDS", 30.0)
        )
        self.strict = (
            strict
            if strict is not None
            else _truthy(os.environ.get("CORE_MEMORY_SEMANTIC_TASK_RUNTIME_STRICT"))
        )
        self.fallback_runtime = fallback_runtime

    def run(self, request: SemanticTaskRequest) -> SemanticTaskResult:
        if not self.url or not self.token:
            return self._fallback_or_unavailable(
                request,
                "remote_runtime_not_configured",
            )

        try:
            payload = _request_payload(request)
            response = _post_json(
                self.url,
                payload,
                token=self.token,
                timeout_seconds=self.timeout_seconds,
            )
            result = _result_from_payload(request, response)
            return _receipted(request, _mark_remote_result(result, response))
        except Exception as exc:  # noqa: BLE001
            return self._fallback_or_unavailable(
                request,
                f"remote_runtime_unavailable:{exc.__class__.__name__}",
            )

    def _fallback_or_unavailable(
        self,
        request: SemanticTaskRequest,
        error: str,
    ) -> SemanticTaskResult:
        if self.strict or self.fallback_runtime is None:
            return _receipted(request, _unavailable_result(request, error))

        fallback_request = replace(
            request,
            root=None,
            fallback_mode=request.fallback_mode or "provider",
        )
        fallback = self.fallback_runtime.run(fallback_request)
        return _receipted(
            request,
            _mark_remote_fallback_result(fallback, request, error),
        )


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    token: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode(
        "utf-8"
    )
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read().decode("utf-8")
    parsed = json.loads(raw or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("Remote semantic task response must be a JSON object.")
    return parsed


def _request_payload(request: SemanticTaskRequest) -> dict[str, Any]:
    payload = asdict(request)
    payload.pop("root", None)
    return payload


def _result_from_payload(
    request: SemanticTaskRequest,
    payload: dict[str, Any],
) -> SemanticTaskResult:
    profile_payload = payload.get("model_profile")
    profile = (
        _model_profile(profile_payload)
        if isinstance(profile_payload, dict)
        else _default_remote_profile(request)
    )
    return SemanticTaskResult(
        task_id=str(payload.get("task_id") or request.task_id or _task_id_basis(request)),
        task_type=str(payload.get("task_type") or request.task_type),
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ("succeeded" if payload.get("ok") else "failed")),
        output_text=str(payload.get("output_text") or ""),
        output_json=payload.get("output_json") if isinstance(payload.get("output_json"), dict) else None,
        model_profile=profile,
        prompt_version=str(payload.get("prompt_version") or request.prompt_version),
        rubric_version=str(payload.get("rubric_version") or request.rubric_version),
        output_schema=str(payload.get("output_schema") or request.output_schema),
        input_hash=str(payload.get("input_hash") or ""),
        output_hash=str(payload.get("output_hash") or ""),
        latency_ms=payload.get("latency_ms") if isinstance(payload.get("latency_ms"), int) else None,
        token_usage=dict(payload.get("token_usage") or {}),
        fallback_mode=str(payload.get("fallback_mode") or request.fallback_mode),
        authority_boundary=str(payload.get("authority_boundary") or request.authority_boundary),
        evidence_refs=list(payload.get("evidence_refs") or request.evidence_refs or []),
        result_refs=dict(payload.get("result_refs") or {}),
        error=str(payload.get("error") or ""),
        metadata=dict(payload.get("metadata") or request.metadata or {}),
        receipt_id=str(payload.get("receipt_id") or ""),
    )


def _model_profile(payload: dict[str, Any]) -> ModelProfile:
    tier = str(payload.get("tier") or MODEL_TIER_STANDARD).strip().lower()
    if tier not in MODEL_TIERS:
        tier = MODEL_TIER_STANDARD
    return ModelProfile(
        tier=tier,
        provider=str(payload.get("provider") or ""),
        adapter=str(payload.get("adapter") or ""),
        model=str(payload.get("model") or ""),
        runtime="remote",
        source=str(payload.get("source") or ""),
    )


def _default_remote_profile(request: SemanticTaskRequest) -> ModelProfile:
    tier = str(
        request.model_tier
        or DEFAULT_TASK_MODEL_TIERS.get(request.task_type)
        or MODEL_TIER_STANDARD
    ).strip().lower()
    if tier not in MODEL_TIERS:
        tier = MODEL_TIER_STANDARD
    return ModelProfile(tier=tier, runtime="remote")


def _unavailable_result(
    request: SemanticTaskRequest,
    error: str,
) -> SemanticTaskResult:
    return SemanticTaskResult(
        task_id=request.task_id or f"semtask-{_task_id_basis(request)}",
        task_type=request.task_type,
        ok=False,
        status="unavailable",
        model_profile=_default_remote_profile(request),
        prompt_version=request.prompt_version,
        rubric_version=request.rubric_version,
        output_schema=request.output_schema,
        input_hash=stable_hash(
            {
                "task_type": request.task_type,
                "prompt": request.prompt,
                "payload": request.payload,
            }
        ),
        output_hash=stable_hash({"error": error}),
        fallback_mode=request.fallback_mode,
        authority_boundary=request.authority_boundary,
        evidence_refs=list(request.evidence_refs or []),
        error=error,
        metadata={"runtime_mode": "remote", **dict(request.metadata or {})},
    )


def _mark_remote_result(
    result: SemanticTaskResult,
    payload: dict[str, Any],
) -> SemanticTaskResult:
    refs = dict(result.result_refs or {})
    conductor_receipt_id = str(payload.get("receipt_id") or "")
    if conductor_receipt_id:
        refs.setdefault("conductor_receipt_id", conductor_receipt_id)
    return SemanticTaskResult(
        **{
            **result.as_dict(),
            "model_profile": _profile_with_runtime(result.model_profile, "remote"),
            "metadata": {"runtime_mode": "remote", **dict(result.metadata or {})},
            "result_refs": refs,
        }
    )


def _mark_remote_fallback_result(
    result: SemanticTaskResult,
    request: SemanticTaskRequest,
    error: str,
) -> SemanticTaskResult:
    metadata = {
        **dict(result.metadata or {}),
        **dict(request.metadata or {}),
        "runtime_mode": "remote_fallback",
        "remote_error": error,
    }
    return SemanticTaskResult(
        **{
            **result.as_dict(),
            "model_profile": _profile_with_runtime(
                result.model_profile or _default_remote_profile(request),
                "remote_fallback",
            ),
            "fallback_mode": result.fallback_mode or "provider",
            "metadata": metadata,
        }
    )


def _profile_with_runtime(profile: ModelProfile | None, runtime: str) -> ModelProfile:
    if profile is None:
        return ModelProfile(tier=MODEL_TIER_STANDARD, runtime=runtime)
    return ModelProfile(
        tier=profile.tier,
        provider=profile.provider,
        adapter=profile.adapter,
        model=profile.model,
        runtime=runtime,
        source=profile.source,
    )


def _receipted(
    request: SemanticTaskRequest,
    result: SemanticTaskResult,
) -> SemanticTaskResult:
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


def _task_id_basis(request: SemanticTaskRequest) -> str:
    return request.idempotency_key or stable_hash(
        {
            "task_type": request.task_type,
            "prompt": request.prompt,
            "payload": request.payload,
        }
    )


def _float_env(name: str, fallback: float) -> float:
    try:
        return float(os.environ.get(name) or fallback)
    except (TypeError, ValueError):
        return fallback


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["RemoteSemanticTaskRuntime"]
