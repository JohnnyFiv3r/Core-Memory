"""F-RW1 acceptance tests: pluggable tokenizer for rolling window budget.

Verifies:
1. estimate_tokens no longer uses len(text)//4.
2. Tokenizer is selectable via CORE_MEMORY_TOKENIZER env var.
3. Chars-per-token fallback is configurable and uses provider-aware defaults.
4. tiktoken produces accurate counts when available.
5. Rolling window uses the new tokenizer (not the old hardcoded estimate).
"""

import os
import unittest
from unittest.mock import patch

from core_memory.write_pipeline.tokenizer import (
    _chars_per_token,
    _resolve_tokenizer_type,
    estimate_tokens,
    reset_tokenizer_cache,
)


class TestTokenizerSelection(unittest.TestCase):
    """Tokenizer backend is selected by config."""

    def setUp(self):
        reset_tokenizer_cache()

    def tearDown(self):
        reset_tokenizer_cache()

    @patch.dict(os.environ, {"CORE_MEMORY_TOKENIZER": "tiktoken"}, clear=False)
    def test_explicit_tiktoken(self):
        self.assertEqual(_resolve_tokenizer_type(), "tiktoken")

    @patch.dict(os.environ, {"CORE_MEMORY_TOKENIZER": "chars"}, clear=False)
    def test_explicit_chars(self):
        self.assertEqual(_resolve_tokenizer_type(), "chars")

    @patch.dict(os.environ, {"CORE_MEMORY_TOKENIZER": "transformers"}, clear=False)
    def test_explicit_transformers(self):
        self.assertEqual(_resolve_tokenizer_type(), "transformers")

    def test_auto_detect_openai(self):
        with patch.dict(os.environ, {
            "CORE_MEMORY_EMBEDDINGS_PROVIDER": "openai",
        }, clear=False):
            os.environ.pop("CORE_MEMORY_TOKENIZER", None)
            reset_tokenizer_cache()
            self.assertEqual(_resolve_tokenizer_type(), "tiktoken")

    def test_auto_detect_gemini_falls_to_chars(self):
        with patch.dict(os.environ, {
            "CORE_MEMORY_EMBEDDINGS_PROVIDER": "gemini",
        }, clear=False):
            os.environ.pop("CORE_MEMORY_TOKENIZER", None)
            reset_tokenizer_cache()
            self.assertEqual(_resolve_tokenizer_type(), "chars")

    def test_no_config_falls_to_chars(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORE_MEMORY_TOKENIZER", None)
            os.environ.pop("CORE_MEMORY_EMBEDDINGS_PROVIDER", None)
            reset_tokenizer_cache()
            self.assertEqual(_resolve_tokenizer_type(), "chars")


class TestCharsPerToken(unittest.TestCase):
    """Chars-per-token ratio is configurable and provider-aware."""

    def test_explicit_override(self):
        with patch.dict(os.environ, {"CORE_MEMORY_CHARS_PER_TOKEN": "5.0"}, clear=False):
            self.assertAlmostEqual(_chars_per_token(), 5.0)

    def test_openai_default(self):
        with patch.dict(os.environ, {"CORE_MEMORY_EMBEDDINGS_PROVIDER": "openai"}, clear=False):
            os.environ.pop("CORE_MEMORY_CHARS_PER_TOKEN", None)
            self.assertAlmostEqual(_chars_per_token(), 4.0)

    def test_anthropic_default(self):
        with patch.dict(os.environ, {"CORE_MEMORY_EMBEDDINGS_PROVIDER": "anthropic"}, clear=False):
            os.environ.pop("CORE_MEMORY_CHARS_PER_TOKEN", None)
            self.assertAlmostEqual(_chars_per_token(), 3.5)

    def test_no_provider_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORE_MEMORY_CHARS_PER_TOKEN", None)
            os.environ.pop("CORE_MEMORY_EMBEDDINGS_PROVIDER", None)
            self.assertAlmostEqual(_chars_per_token(), 3.7)


class TestEstimateTokens(unittest.TestCase):
    """Token estimation works correctly across backends."""

    def setUp(self):
        reset_tokenizer_cache()

    def tearDown(self):
        reset_tokenizer_cache()

    def test_empty_string_returns_one(self):
        self.assertEqual(estimate_tokens(""), 1)

    def test_always_returns_at_least_one(self):
        self.assertGreaterEqual(estimate_tokens("x"), 1)

    @patch.dict(os.environ, {"CORE_MEMORY_TOKENIZER": "chars", "CORE_MEMORY_CHARS_PER_TOKEN": "4.0"}, clear=False)
    def test_chars_mode_basic(self):
        # 40 chars / 4.0 ratio = 10 tokens
        text = "a" * 40
        self.assertEqual(estimate_tokens(text), 10)

    def test_not_len_div_4(self):
        """Verify we're NOT using the old len(text)//4 estimate."""
        with patch.dict(os.environ, {"CORE_MEMORY_TOKENIZER": "chars"}, clear=False):
            reset_tokenizer_cache()
            text = "a" * 100
            old_estimate = max(1, len(text) // 4)  # 25
            new_estimate = estimate_tokens(text)
            # With default 3.7 chars/token, new should be ~27, not 25
            # The key assertion: they differ (proving we're not using //4)
            self.assertNotEqual(new_estimate, old_estimate)


try:
    import tiktoken  # noqa: F401
    _HAS_TIKTOKEN = True
except ImportError:
    _HAS_TIKTOKEN = False


@unittest.skipUnless(_HAS_TIKTOKEN, "tiktoken not installed")
class TestTiktokenBackend(unittest.TestCase):
    """tiktoken produces accurate counts."""

    def setUp(self):
        reset_tokenizer_cache()

    def tearDown(self):
        reset_tokenizer_cache()

    @patch.dict(os.environ, {"CORE_MEMORY_TOKENIZER": "tiktoken"}, clear=False)
    def test_tiktoken_counts_real_tokens(self):
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        text = "The quick brown fox jumps over the lazy dog."
        expected = len(enc.encode(text))
        self.assertEqual(estimate_tokens(text), expected)

    @patch.dict(os.environ, {"CORE_MEMORY_TOKENIZER": "tiktoken"}, clear=False)
    def test_tiktoken_code_vs_prose(self):
        prose = "This is a simple English sentence about memory systems."
        code = "def estimate_tokens(text: str) -> int:\n    return max(1, len(text) // 4)"
        prose_tokens = estimate_tokens(prose)
        code_tokens = estimate_tokens(code)
        # Code typically has more tokens per character than prose
        prose_ratio = len(prose) / prose_tokens
        code_ratio = len(code) / code_tokens
        self.assertGreater(prose_ratio, code_ratio)


class TestRollingWindowIntegration(unittest.TestCase):
    """Rolling window uses the new tokenizer."""

    def test_rolling_window_imports_new_tokenizer(self):
        from core_memory.write_pipeline import rolling_window
        # The estimate_tokens in rolling_window should be our new one
        self.assertIs(
            rolling_window.estimate_tokens,
            estimate_tokens,
        )


if __name__ == "__main__":
    unittest.main()
