"""Explicit provider:model resolution with no defaults or substitutions."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from core_memory.llm_client import chat_complete
from core_memory.provider_config import ProviderConfig, default_base_url, normalize_provider

from .constants import AUTHOR_MODEL_ENV_KEYS, AUTHOR_PROVIDER_ENV_KEYS
from .models import ModelAdapter, ModelRequest, ModelResponse, ModelSelection, ResolvedModel


class ModelBlockedError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ModelExecutionError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_PROVIDER_ALIASES = {
    "claude": "anthropic",
    "gemini": "google",
    "lm-studio": "lmstudio",
    "openai_compatible": "openai-compatible",
    "llama.cpp": "llamacpp",
}

_SUPPORTED_PROVIDERS = {
    "anthropic",
    "google",
    "llamacpp",
    "lmstudio",
    "ollama",
    "openai",
    "openai-compatible",
    "openrouter",
    "vllm",
}


def canonical_provider(provider: str) -> str:
    raw = str(provider or "").strip().lower()
    return _PROVIDER_ALIASES.get(raw, raw)


def _first_env(*names: str) -> str:
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _provider_config(resolved: ResolvedModel) -> ProviderConfig:
    provider = resolved.provider
    adapter = normalize_provider(provider)
    base_url = _first_env("CORE_MEMORY_CHAT_BASE_URL", "CORE_MEMORY_LLM_BASE_URL")
    api_key = _first_env("CORE_MEMORY_CHAT_API_KEY", "CORE_MEMORY_LLM_API_KEY")

    if provider == "openai":
        base_url = base_url or default_base_url("openai")
        api_key = api_key or _first_env("OPENAI_API_KEY")
    elif provider == "openrouter":
        base_url = base_url or default_base_url("openrouter")
        api_key = api_key or _first_env("OPENROUTER_API_KEY")
    elif provider in {"ollama", "lmstudio", "vllm", "llamacpp"}:
        base_url = base_url or default_base_url(provider)
    elif provider == "openai-compatible":
        if not base_url:
            raise ModelBlockedError("missing_openai_compatible_base_url")
        api_key = api_key or _first_env("OPENAI_API_KEY", "OPENROUTER_API_KEY")
    elif provider == "anthropic":
        api_key = api_key or _first_env("ANTHROPIC_API_KEY")
    elif provider == "google":
        api_key = api_key or _first_env("GEMINI_API_KEY", "GOOGLE_API_KEY")

    if provider in {"openai", "openrouter", "anthropic", "google"} and not api_key:
        raise ModelBlockedError(f"missing_credentials:{provider}")
    if adapter == "openai-compatible" and not base_url:
        raise ModelBlockedError(f"missing_base_url:{provider}")

    return ProviderConfig(
        kind="chat",
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=resolved.model,
        source="benchmark_selector",
        explicit=True,
    )


class ProviderModelAdapter:
    """Adapter over Core Memory's provider-neutral chat client."""

    def resolve(self, selection: ModelSelection) -> ResolvedModel:
        provider = canonical_provider(selection.provider)
        if provider not in _SUPPORTED_PROVIDERS:
            raise ModelBlockedError(f"unsupported_provider:{provider or 'missing'}")
        resolved = ResolvedModel(
            requested=selection.requested,
            provider=provider,
            model=selection.model,
            adapter=normalize_provider(provider),
        )
        _provider_config(resolved)
        return resolved

    def complete(self, request: ModelRequest, resolved: ResolvedModel) -> ModelResponse:
        config = _provider_config(resolved)
        started = time.perf_counter()
        try:
            text = chat_complete(
                json.dumps(request.payload, ensure_ascii=False, sort_keys=True),
                config=config,
                max_tokens=max(1, int(request.max_tokens)),
                temperature=0,
                json_mode=True,
            )
        except Exception as exc:  # noqa: BLE001 - provider errors are normalized and redacted
            raise ModelExecutionError(f"provider_request_failed:{exc.__class__.__name__}") from None
        return ModelResponse(
            text=text,
            resolved_model=resolved,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )


@dataclass
class ModelRegistry:
    adapters: dict[str, ModelAdapter]

    @classmethod
    def default(cls) -> ModelRegistry:
        adapter = ProviderModelAdapter()
        return cls(adapters={provider: adapter for provider in sorted(_SUPPORTED_PROVIDERS)})

    def register(self, provider: str, adapter: ModelAdapter) -> None:
        self.adapters[canonical_provider(provider)] = adapter

    def resolve(self, value: str | None) -> tuple[ResolvedModel, ModelAdapter]:
        try:
            selection = ModelSelection.parse(value)
        except ValueError as exc:
            raise ModelBlockedError(str(exc)) from None
        provider = canonical_provider(selection.provider)
        adapter = self.adapters.get(provider)
        if adapter is None:
            raise ModelBlockedError(f"unsupported_provider:{provider}")
        canonical = ModelSelection(
            requested=selection.requested,
            provider=provider,
            model=selection.model,
        )
        return adapter.resolve(canonical), adapter


@contextmanager
def bind_author_model(resolved: ResolvedModel) -> Iterator[dict[str, str]]:
    """Bind one explicit author selection to every legacy semantic model role."""

    config = _provider_config(resolved) if resolved.baseline_eligible else None
    overrides: dict[str, str] = {
        **{key: resolved.model for key in AUTHOR_MODEL_ENV_KEYS},
        **{key: resolved.provider for key in AUTHOR_PROVIDER_ENV_KEYS},
        "CORE_MEMORY_SEMANTIC_TASK_RUNTIME": "provider",
    }
    if config is not None:
        if config.base_url:
            overrides["CORE_MEMORY_CHAT_BASE_URL"] = config.base_url
            overrides["CORE_MEMORY_LLM_BASE_URL"] = config.base_url
        if config.api_key:
            overrides["CORE_MEMORY_CHAT_API_KEY"] = config.api_key
            overrides["CORE_MEMORY_LLM_API_KEY"] = config.api_key

    saved = {key: os.environ.get(key) for key in overrides}
    try:
        os.environ.update(overrides)
        yield {key: resolved.identifier for key in AUTHOR_MODEL_ENV_KEYS}
    finally:
        for key, prior in saved.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior


def parse_json_response(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.strip().startswith("json"):
            raw = raw.strip()[4:]
    try:
        payload = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        raise ModelExecutionError("invalid_model_json") from exc
    if not isinstance(payload, dict):
        raise ModelExecutionError("model_output_not_object")
    return payload


def complete_json(
    adapter: ModelAdapter,
    resolved: ResolvedModel,
    request: ModelRequest,
    *,
    attempts: int = 3,
) -> tuple[dict[str, Any], list[ModelResponse]]:
    receipts: list[ModelResponse] = []
    last_code = "model_execution_failed"
    for _attempt in range(max(1, int(attempts))):
        try:
            response = adapter.complete(request, resolved)
            receipts.append(response)
            return parse_json_response(response.text), receipts
        except ModelExecutionError as exc:
            last_code = exc.code
    raise ModelExecutionError(f"retries_exhausted:{last_code}")
