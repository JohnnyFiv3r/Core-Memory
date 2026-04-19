"""Pluggable token estimation for rolling window budget calculations.

Selects tokenizer based on configuration:
  1. CORE_MEMORY_TOKENIZER env var (explicit: tiktoken, transformers, or chars)
  2. Auto-detect from CORE_MEMORY_EMBEDDINGS_PROVIDER (openai → tiktoken, etc.)
  3. Fallback: configurable chars-per-token ratio

The chars-per-token fallback is tuned per model family:
  - OpenAI / tiktoken models: ~4.0 chars/token
  - Claude / Anthropic: ~3.5 chars/token
  - Code-heavy content: ~3.0 chars/token
  - Default: 3.7 chars/token (conservative middle ground)
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN_DEFAULTS = {
    "openai": 4.0,
    "anthropic": 3.5,
    "claude": 3.5,
    "gemini": 4.0,
    "code": 3.0,
    "default": 3.7,
}

_tiktoken_encoder = None
_tokenizer_type: Optional[str] = None


def _get_tiktoken_encoder():
    global _tiktoken_encoder
    if _tiktoken_encoder is not None:
        return _tiktoken_encoder
    try:
        import tiktoken
        model = os.environ.get("CORE_MEMORY_TIKTOKEN_MODEL", "cl100k_base").strip()
        _tiktoken_encoder = tiktoken.get_encoding(model)
        return _tiktoken_encoder
    except Exception as exc:
        logger.debug("tokenizer: tiktoken unavailable: %s", exc)
        return None


def _resolve_tokenizer_type() -> str:
    """Resolve which tokenizer to use, caching the result."""
    global _tokenizer_type
    if _tokenizer_type is not None:
        return _tokenizer_type

    explicit = os.environ.get("CORE_MEMORY_TOKENIZER", "").strip().lower()
    if explicit in {"tiktoken", "transformers", "chars"}:
        _tokenizer_type = explicit
        return _tokenizer_type

    # Auto-detect from embedding provider
    provider = os.environ.get("CORE_MEMORY_EMBEDDINGS_PROVIDER", "").strip().lower()
    if provider in {"openai"}:
        _tokenizer_type = "tiktoken"
    elif provider in {"gemini", "hash", ""}:
        _tokenizer_type = "chars"
    else:
        _tokenizer_type = "chars"

    return _tokenizer_type


def _chars_per_token() -> float:
    """Get the chars-per-token ratio for the fallback estimator."""
    explicit = os.environ.get("CORE_MEMORY_CHARS_PER_TOKEN", "").strip()
    if explicit:
        try:
            val = float(explicit)
            if val > 0:
                return val
        except ValueError:
            pass

    provider = os.environ.get("CORE_MEMORY_EMBEDDINGS_PROVIDER", "").strip().lower()
    return _CHARS_PER_TOKEN_DEFAULTS.get(provider, _CHARS_PER_TOKEN_DEFAULTS["default"])


def estimate_tokens(text: str) -> int:
    """Estimate the token count for a text string.

    Uses the configured tokenizer backend. Always returns at least 1.
    """
    if not text:
        return 1

    tokenizer = _resolve_tokenizer_type()

    if tokenizer == "tiktoken":
        enc = _get_tiktoken_encoder()
        if enc is not None:
            return max(1, len(enc.encode(text)))
        # Fall through to chars if tiktoken failed to load

    if tokenizer == "transformers":
        try:
            from transformers import AutoTokenizer  # type: ignore
            model_name = os.environ.get("CORE_MEMORY_HF_TOKENIZER_MODEL", "bert-base-uncased").strip()
            tok = AutoTokenizer.from_pretrained(model_name)
            return max(1, len(tok.encode(text)))
        except Exception:
            pass
        # Fall through to chars

    # Default: chars-per-token ratio
    ratio = _chars_per_token()
    return max(1, int(len(text) / ratio))


def reset_tokenizer_cache() -> None:
    """Reset cached tokenizer state. Useful for testing."""
    global _tiktoken_encoder, _tokenizer_type
    _tiktoken_encoder = None
    _tokenizer_type = None
