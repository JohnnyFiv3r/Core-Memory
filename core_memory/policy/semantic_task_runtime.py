"""Semantic task runtime factory and provider-neutral implementation."""

from __future__ import annotations

import json
import os
import time
import uuid
from importlib import import_module
from typing import Any

from core_memory.llm_client import chat_complete
from core_memory.persistence.semantic_task_receipts import record_semantic_task_run, stable_hash
from core_memory.provider_config import ProviderConfig, resolve_chat_config
from core_memory.schema.semantic_tasks import (
    DEFAULT_TASK_MODEL_TIERS,
    MODEL_TIER_CHEAP,
    MODEL_TIER_FRONTIER,
    MODEL_TIER_STANDARD,
    MODEL_TIERS,
    SEMANTIC_TASK_TYPES,
    TASK_ASSOCIATION_DECISION,
    TASK_BEAD_FIELD_JUDGE,
    TASK_BEAD_TYPE_CLASSIFIER,
    TASK_CAUSAL_RECALL_EXECUTE,
    TASK_RATIONALE_EXTRACTOR,
    TASK_TURN_MEMORY_AUTHORING,
    ModelProfile,
    SemanticTaskRequest,
    SemanticTaskResult,
    SemanticTaskRuntime,
    TaskProfile,
)


def _env_first(*names: str) -> tuple[str, str]:
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value, name
    return "", ""


def semantic_task_runtime_mode() -> str:
    mode = (
        str(
            os.environ.get("CORE_MEMORY_SEMANTIC_TASK_RUNTIME")
            or os.environ.get("CORE_MEMORY_SEMANTIC_RUNTIME")
            or "auto"
        )
        .strip()
        .lower()
    )
    if mode in {
        "",
        "auto",
        "provider",
        "llm",
        "pydanticai",
        "pydantic-ai",
        "remote",
        "delegated",
        "disabled",
        "off",
    }:
        if mode == "delegated":
            return "remote"
        return "pydanticai" if mode == "pydantic-ai" else (mode or "auto")
    return "auto"


def task_profile(
    task_type: str,
    *,
    prompt_version: str = "",
    rubric_version: str = "",
    output_schema: str = "",
) -> TaskProfile:
    normalized = str(task_type or "").strip()
    tier = DEFAULT_TASK_MODEL_TIERS.get(normalized, MODEL_TIER_STANDARD)
    return TaskProfile(
        task_type=normalized,
        model_tier=tier,
        prompt_version=prompt_version,
        rubric_version=rubric_version,
        output_schema=output_schema,
        authority_boundary=(
            "semantic_author"
            if normalized in {TASK_TURN_MEMORY_AUTHORING, TASK_ASSOCIATION_DECISION}
            else "candidate_only"
            if normalized in {"dreamer_research", "soul_proposal"}
            else "advisory"
        ),
    )


def _model_for_task(task_type: str, tier: str) -> tuple[str, str]:
    normalized = str(tier or "").strip().lower()
    if normalized == MODEL_TIER_CHEAP:
        task = str(task_type or "").strip()
        if task == TASK_RATIONALE_EXTRACTOR:
            return _env_first(
                "CORE_MEMORY_AGENT_MODEL_CHEAP",
                "CORE_MEMORY_BECAUSE_MODEL",
                "CORE_MEMORY_BEAD_TYPE_MODEL",
                "CORE_MEMORY_BEAD_FIELD_MODEL",
            )
        if task == TASK_BEAD_TYPE_CLASSIFIER:
            return _env_first(
                "CORE_MEMORY_AGENT_MODEL_CHEAP",
                "CORE_MEMORY_BEAD_TYPE_MODEL",
                "CORE_MEMORY_BEAD_FIELD_MODEL",
                "CORE_MEMORY_BECAUSE_MODEL",
            )
        if task == TASK_BEAD_FIELD_JUDGE:
            return _env_first(
                "CORE_MEMORY_AGENT_MODEL_CHEAP",
                "CORE_MEMORY_BEAD_FIELD_MODEL",
                "CORE_MEMORY_BECAUSE_MODEL",
                "CORE_MEMORY_BEAD_TYPE_MODEL",
            )
        return _env_first("CORE_MEMORY_AGENT_MODEL_CHEAP", "CORE_MEMORY_BEAD_FIELD_MODEL", "CORE_MEMORY_BECAUSE_MODEL")
    if normalized == MODEL_TIER_FRONTIER:
        return _env_first("CORE_MEMORY_AGENT_MODEL_FRONTIER", "CORE_MEMORY_DREAMER_MODEL")
    task = str(task_type or "").strip()
    if task == TASK_TURN_MEMORY_AUTHORING:
        return _env_first(
            "CORE_MEMORY_TURN_MEMORY_AUTHOR_MODEL",
            "CORE_MEMORY_AGENT_MODEL_STANDARD",
            "CORE_MEMORY_BEAD_FIELD_MODEL",
            "CORE_MEMORY_CHAT_MODEL",
        )
    if task == TASK_BEAD_FIELD_JUDGE:
        # The full-schema compat judge runs at standard tier (it authors an
        # entire bead); the explicit bead-field model override still wins.
        return _env_first(
            "CORE_MEMORY_BEAD_FIELD_MODEL",
            "CORE_MEMORY_AGENT_MODEL_STANDARD",
            "CORE_MEMORY_CHAT_MODEL",
        )
    if task == TASK_CAUSAL_RECALL_EXECUTE:
        return _env_first(
            "CORE_MEMORY_AGENT_MODEL_STANDARD",
            "CORE_MEMORY_RECALL_MODEL",
            "CORE_MEMORY_CHAT_MODEL",
            "CORE_MEMORY_ASSOCIATION_JUDGE_MODEL",
        )
    return _env_first(
        "CORE_MEMORY_AGENT_MODEL_STANDARD",
        "CORE_MEMORY_ASSOCIATION_JUDGE_MODEL",
        "CORE_MEMORY_CHAT_MODEL",
    )


def resolve_model_profile(
    task_type: str,
    *,
    runtime: str = "provider",
    config: ProviderConfig | None = None,
    model_tier: str = "",
) -> ModelProfile:
    cfg = config or resolve_chat_config()
    tier = str(model_tier or DEFAULT_TASK_MODEL_TIERS.get(str(task_type or ""), MODEL_TIER_STANDARD)).strip().lower()
    if tier not in MODEL_TIERS:
        tier = MODEL_TIER_STANDARD
    model, source = _model_for_task(task_type, tier)
    if not model:
        model = cfg.model
        source = cfg.source
    return ModelProfile(
        tier=tier,
        provider=cfg.provider,
        adapter=cfg.adapter,
        model=model,
        runtime=runtime,
        source=source or cfg.source,
    )


def _with_model(cfg: ProviderConfig, model: str) -> ProviderConfig:
    if not model or model == cfg.model:
        return cfg
    return ProviderConfig(
        kind=cfg.kind,
        provider=cfg.provider,
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        model=model,
        embedding_model=cfg.embedding_model,
        source=cfg.source,
        explicit=cfg.explicit,
    )


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


def _task_id(request: SemanticTaskRequest) -> str:
    if request.task_id:
        return str(request.task_id)
    basis = request.idempotency_key or stable_hash(
        {"task_type": request.task_type, "prompt": request.prompt, "payload": request.payload}
    )
    return f"semtask-{basis}-{uuid.uuid4().hex[:8]}"


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


class DisabledSemanticTaskRuntime:
    def run(self, request: SemanticTaskRequest) -> SemanticTaskResult:
        task_id = _task_id(request)
        result = SemanticTaskResult(
            task_id=task_id,
            task_type=request.task_type,
            ok=False,
            status="unavailable",
            input_hash=stable_hash(
                {"task_type": request.task_type, "prompt": request.prompt, "payload": request.payload}
            ),
            output_hash=stable_hash({"error": "semantic_task_runtime_disabled"}),
            fallback_mode=request.fallback_mode,
            authority_boundary=request.authority_boundary,
            evidence_refs=list(request.evidence_refs or []),
            error="semantic_task_runtime_disabled",
            metadata={"runtime_mode": "disabled", **dict(request.metadata or {})},
        )
        return _receipted(request, result)


class ProviderSemanticTaskRuntime:
    def run(self, request: SemanticTaskRequest) -> SemanticTaskResult:
        task_type = str(request.task_type or "").strip()
        task_id = _task_id(request)
        if task_type not in SEMANTIC_TASK_TYPES:
            result = SemanticTaskResult(
                task_id=task_id,
                task_type=task_type,
                ok=False,
                status="failed",
                input_hash=stable_hash({"task_type": task_type, "prompt": request.prompt, "payload": request.payload}),
                output_hash=stable_hash({"error": "unknown_semantic_task_type"}),
                error="unknown_semantic_task_type",
                metadata=dict(request.metadata or {}),
            )
            return _receipted(request, result)

        cfg = resolve_chat_config()
        profile = resolve_model_profile(task_type, runtime="provider", config=cfg, model_tier=request.model_tier)
        if not cfg.provider:
            result = SemanticTaskResult(
                task_id=task_id,
                task_type=task_type,
                ok=False,
                status="unavailable",
                model_profile=profile,
                prompt_version=request.prompt_version,
                rubric_version=request.rubric_version,
                output_schema=request.output_schema,
                input_hash=stable_hash({"task_type": task_type, "prompt": request.prompt, "payload": request.payload}),
                output_hash=stable_hash({"error": "missing_chat_provider"}),
                fallback_mode=request.fallback_mode,
                authority_boundary=request.authority_boundary,
                evidence_refs=list(request.evidence_refs or []),
                error="missing_chat_provider",
                metadata={"runtime_mode": semantic_task_runtime_mode(), **dict(request.metadata or {})},
            )
            return _receipted(request, result)

        prompt = request.prompt or json.dumps(request.payload, ensure_ascii=False, sort_keys=True)
        started = time.perf_counter()
        try:
            text = chat_complete(
                prompt,
                config=_with_model(cfg, profile.model),
                max_tokens=max(1, int(request.max_tokens or 700)),
                temperature=float(request.temperature or 0),
                json_mode=bool(request.json_mode),
            )
            parsed = _parse_json(text) if request.json_mode else None
            latency_ms = int((time.perf_counter() - started) * 1000)
            result = SemanticTaskResult(
                task_id=task_id,
                task_type=task_type,
                ok=True,
                status="succeeded",
                output_text=str(text or ""),
                output_json=parsed,
                model_profile=profile,
                prompt_version=request.prompt_version,
                rubric_version=request.rubric_version,
                output_schema=request.output_schema,
                input_hash=stable_hash({"task_type": task_type, "prompt": prompt, "payload": request.payload}),
                output_hash=stable_hash({"output_text": text, "output_json": parsed}),
                latency_ms=latency_ms,
                fallback_mode=request.fallback_mode,
                authority_boundary=request.authority_boundary,
                evidence_refs=list(request.evidence_refs or []),
                metadata={"runtime_mode": semantic_task_runtime_mode(), **dict(request.metadata or {})},
            )
            return _receipted(request, result)
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.perf_counter() - started) * 1000)
            result = SemanticTaskResult(
                task_id=task_id,
                task_type=task_type,
                ok=False,
                status="failed",
                model_profile=profile,
                prompt_version=request.prompt_version,
                rubric_version=request.rubric_version,
                output_schema=request.output_schema,
                input_hash=stable_hash({"task_type": task_type, "prompt": prompt, "payload": request.payload}),
                output_hash=stable_hash({"error": str(exc)}),
                latency_ms=latency_ms,
                fallback_mode=request.fallback_mode,
                authority_boundary=request.authority_boundary,
                evidence_refs=list(request.evidence_refs or []),
                error=str(exc),
                metadata={"runtime_mode": semantic_task_runtime_mode(), **dict(request.metadata or {})},
            )
            return _receipted(request, result)


def _pydanticai_semantic_task_runtime() -> SemanticTaskRuntime:
    module = import_module("core_memory.integrations.pydanticai.semantic_tasks")
    runtime_cls = getattr(module, "PydanticAISemanticTaskRuntime")
    return runtime_cls()


def _remote_semantic_task_runtime() -> SemanticTaskRuntime:
    module = import_module("core_memory.integrations.remote.semantic_tasks")
    runtime_cls = getattr(module, "RemoteSemanticTaskRuntime")
    return runtime_cls(fallback_runtime=ProviderSemanticTaskRuntime())


def get_semantic_task_runtime(*, mode: str | None = None) -> SemanticTaskRuntime:
    resolved = str(mode or semantic_task_runtime_mode()).strip().lower()
    if resolved in {"disabled", "off"}:
        return DisabledSemanticTaskRuntime()
    if resolved in {"provider", "llm", "auto"}:
        return ProviderSemanticTaskRuntime()
    if resolved == "pydanticai":
        try:
            return _pydanticai_semantic_task_runtime()
        except Exception:
            return DisabledSemanticTaskRuntime()
    if resolved == "remote":
        try:
            return _remote_semantic_task_runtime()
        except Exception:
            return ProviderSemanticTaskRuntime()
    return ProviderSemanticTaskRuntime()


__all__ = [
    "DisabledSemanticTaskRuntime",
    "ProviderSemanticTaskRuntime",
    "get_semantic_task_runtime",
    "resolve_model_profile",
    "semantic_task_runtime_mode",
    "task_profile",
]
