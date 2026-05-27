from __future__ import annotations

"""Provider-neutral chat and embedding configuration.

Core Memory is local-first: no hosted provider is assumed.  OpenAI-compatible
HTTP endpoints (OpenAI, OpenRouter, Ollama, LM Studio, vLLM, llama.cpp, etc.)
are first-class peers alongside Anthropic and Google adapters.
"""

from dataclasses import dataclass
import os
from typing import Any

OPENAI_COMPATIBLE_PROVIDERS = {
    "openai",
    "openai-compatible",
    "openai_compatible",
    "openrouter",
    "ollama",
    "lmstudio",
    "lm-studio",
    "vllm",
    "llamacpp",
    "llama.cpp",
}
GOOGLE_PROVIDERS = {"google", "gemini"}
ANTHROPIC_PROVIDERS = {"anthropic", "claude"}
LOCAL_PROVIDERS = {"hash", "local", "none"}

_PROVIDER_ALIASES = {
    "openai_compatible": "openai-compatible",
    "openai-compatible": "openai-compatible",
    "openrouter": "openai-compatible",
    "ollama": "openai-compatible",
    "lmstudio": "openai-compatible",
    "lm-studio": "openai-compatible",
    "vllm": "openai-compatible",
    "llamacpp": "openai-compatible",
    "llama.cpp": "openai-compatible",
    "google": "google",
    "gemini": "google",
    "claude": "anthropic",
    "anthropic": "anthropic",
    "openai": "openai-compatible",
    "hash": "hash",
    "local": "hash",
    "none": "none",
}

_DEFAULT_CHAT_MODELS = {
    "openai-compatible": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5-20251001",
    "google": "gemini-1.5-flash",
}
_DEFAULT_EMBEDDING_MODELS = {
    "openai-compatible": "text-embedding-3-small",
    "google": "gemini-embedding-001",
    "hash": "hash",
}
_DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
    "lm-studio": "http://localhost:1234/v1",
    "vllm": "http://localhost:8000/v1",
    "llamacpp": "http://localhost:8080/v1",
    "llama.cpp": "http://localhost:8080/v1",
}


@dataclass(frozen=True)
class ProviderConfig:
    kind: str
    provider: str
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    embedding_model: str = ""
    source: str = ""
    explicit: bool = False

    @property
    def adapter(self) -> str:
        return normalize_provider(self.provider)

    @property
    def is_openai_compatible(self) -> bool:
        return self.adapter == "openai-compatible"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "provider": self.provider,
            "adapter": self.adapter,
            "base_url": self.base_url,
            "api_key_configured": bool(self.api_key),
            "model": self.model,
            "embedding_model": self.embedding_model,
            "source": self.source,
            "explicit": self.explicit,
        }


def _env_first(*names: str) -> tuple[str, str]:
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value, name
    return "", ""


def normalize_provider(provider: str) -> str:
    raw = str(provider or "").strip().lower()
    return _PROVIDER_ALIASES.get(raw, raw)


def default_base_url(provider: str) -> str:
    return _DEFAULT_BASE_URLS.get(str(provider or "").strip().lower(), "")


def default_chat_model(provider: str) -> str:
    return _DEFAULT_CHAT_MODELS.get(normalize_provider(provider), "")


def default_embedding_model(provider: str) -> str:
    return _DEFAULT_EMBEDDING_MODELS.get(normalize_provider(provider), "")


def provider_extra_hint(provider: str) -> str:
    adapter = normalize_provider(provider)
    if adapter == "anthropic":
        return "pip install core-memory[anthropic]"
    if adapter == "google":
        return "pip install core-memory[google]"
    if adapter == "openai-compatible":
        return "pip install core-memory[openai]  # optional SDK adapter; HTTP-compatible endpoints work without it"
    return "pip install core-memory[all]"


def resolve_chat_config() -> ProviderConfig:
    provider, source = _env_first("CORE_MEMORY_CHAT_PROVIDER", "CORE_MEMORY_LLM_PROVIDER")
    explicit = bool(provider)
    if not provider:
        if str(os.environ.get("ANTHROPIC_API_KEY") or "").strip():
            provider, source = "anthropic", "ANTHROPIC_API_KEY"
        elif str(os.environ.get("OPENAI_API_KEY") or "").strip():
            provider, source = "openai", "OPENAI_API_KEY"
        elif str(os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or "").strip():
            provider, source = "google", "GOOGLE_API_KEY/GEMINI_API_KEY"
    adapter = normalize_provider(provider)
    base_url, _ = _env_first("CORE_MEMORY_CHAT_BASE_URL", "CORE_MEMORY_LLM_BASE_URL")
    if not base_url:
        base_url = default_base_url(provider)
    api_key, _ = _env_first("CORE_MEMORY_CHAT_API_KEY", "CORE_MEMORY_LLM_API_KEY")
    if not api_key:
        if adapter == "anthropic":
            api_key, _ = _env_first("ANTHROPIC_API_KEY")
        elif adapter == "google":
            api_key, _ = _env_first("GEMINI_API_KEY", "GOOGLE_API_KEY")
        elif adapter == "openai-compatible":
            api_key, _ = _env_first("OPENAI_API_KEY", "OPENROUTER_API_KEY")
    model, _ = _env_first("CORE_MEMORY_CHAT_MODEL", "CORE_MEMORY_LLM_MODEL", "CORE_MEMORY_BEAD_FIELD_MODEL", "CORE_MEMORY_BECAUSE_MODEL", "CORE_MEMORY_BEAD_TYPE_MODEL")
    if not model and provider:
        model = default_chat_model(provider)
    return ProviderConfig("chat", provider=provider, base_url=base_url, api_key=api_key, model=model, source=source, explicit=explicit)


def resolve_embedding_config() -> ProviderConfig:
    provider, source = _env_first("CORE_MEMORY_EMBEDDINGS_PROVIDER", "CORE_MEMORY_EMBEDDING_PROVIDER")
    explicit = bool(provider)
    if not provider:
        if str(os.environ.get("CORE_MEMORY_EMBEDDINGS_BASE_URL") or os.environ.get("CORE_MEMORY_EMBEDDING_BASE_URL") or "").strip():
            provider, source = "openai-compatible", "CORE_MEMORY_EMBEDDINGS_BASE_URL"
        elif str(os.environ.get("OPENAI_API_KEY") or "").strip():
            provider, source = "openai", "OPENAI_API_KEY"
        elif str(os.environ.get("OPENROUTER_API_KEY") or "").strip():
            provider, source = "openrouter", "OPENROUTER_API_KEY"
        elif str(os.environ.get("GEMINI_API_KEY") or "").strip():
            provider, source = "gemini", "GEMINI_API_KEY"
        elif str(os.environ.get("GOOGLE_API_KEY") or "").strip():
            provider, source = "gemini", "GOOGLE_API_KEY"
    adapter = normalize_provider(provider)
    base_url, _ = _env_first("CORE_MEMORY_EMBEDDINGS_BASE_URL", "CORE_MEMORY_EMBEDDING_BASE_URL")
    if not base_url:
        base_url = default_base_url(provider)
    api_key, _ = _env_first("CORE_MEMORY_EMBEDDINGS_API_KEY", "CORE_MEMORY_EMBEDDING_API_KEY")
    if not api_key:
        if adapter == "google":
            api_key, _ = _env_first("GEMINI_API_KEY", "GOOGLE_API_KEY")
        elif adapter == "openai-compatible":
            api_key, _ = _env_first("OPENAI_API_KEY", "OPENROUTER_API_KEY")
    model, _ = _env_first("CORE_MEMORY_EMBEDDINGS_MODEL", "CORE_MEMORY_EMBEDDING_MODEL")
    if not model and provider:
        model = default_embedding_model(provider)
    return ProviderConfig("embeddings", provider=provider, base_url=base_url, api_key=api_key, model=model, embedding_model=model, source=source, explicit=explicit)
