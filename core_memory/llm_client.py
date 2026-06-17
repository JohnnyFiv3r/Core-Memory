from __future__ import annotations

import json
import urllib.request
from typing import Any

from .provider_config import ProviderConfig, resolve_chat_config


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str], *, timeout: int = 45) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - user/provider configured endpoint
        return json.loads(resp.read().decode("utf-8"))


def chat_complete(
    prompt: str,
    *,
    config: ProviderConfig | None = None,
    max_tokens: int = 700,
    temperature: float = 0,
    json_mode: bool = False,
) -> str:
    cfg = config or resolve_chat_config()
    adapter = cfg.adapter
    if not cfg.provider:
        raise RuntimeError("missing_chat_provider")
    if adapter == "openai-compatible":
        if not cfg.base_url:
            raise RuntimeError("missing_openai_compatible_base_url")
        if not cfg.model:
            raise RuntimeError("missing_chat_model")
        headers = {"Authorization": f"Bearer {cfg.api_key or 'local'}"}
        payload: dict[str, Any] = {
            "model": cfg.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        body = _post_json(cfg.base_url.rstrip("/") + "/chat/completions", payload, headers)
        return str((((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""))
    if adapter == "anthropic":
        if not cfg.api_key:
            raise RuntimeError("missing_anthropic_api_key")
        body = _post_json(
            "https://api.anthropic.com/v1/messages",
            {"model": cfg.model, "max_tokens": max_tokens, "temperature": temperature, "messages": [{"role": "user", "content": prompt}]},
            {"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01"},
        )
        content = body.get("content") or []
        return "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    if adapter == "google":
        if not cfg.api_key:
            raise RuntimeError("missing_google_api_key")
        model = cfg.model or "gemini-1.5-flash"
        body = _post_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={cfg.api_key}",
            {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}},
            {},
        )
        candidates = body.get("candidates") or []
        parts = ((((candidates[0] if candidates else {}).get("content") or {}).get("parts") or []))
        return "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
    raise RuntimeError(f"unsupported_chat_provider:{cfg.provider}")
