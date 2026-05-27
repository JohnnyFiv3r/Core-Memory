import os
import unittest
from unittest.mock import patch

from core_memory.provider_config import normalize_provider, resolve_chat_config, resolve_embedding_config


class TestProviderNeutralConfig(unittest.TestCase):
    def test_openai_compatible_aliases_are_peers(self):
        for provider in ["openai", "openrouter", "ollama", "lmstudio", "vllm", "llama.cpp", "openai-compatible"]:
            self.assertEqual("openai-compatible", normalize_provider(provider))

    def test_chat_config_common_shape_for_local_endpoint(self):
        with patch.dict(
            os.environ,
            {
                "CORE_MEMORY_CHAT_PROVIDER": "ollama",
                "CORE_MEMORY_CHAT_BASE_URL": "http://localhost:11434/v1",
                "CORE_MEMORY_CHAT_API_KEY": "local",
                "CORE_MEMORY_CHAT_MODEL": "llama3.1",
            },
            clear=True,
        ):
            cfg = resolve_chat_config()
        self.assertEqual("ollama", cfg.provider)
        self.assertEqual("openai-compatible", cfg.adapter)
        self.assertEqual("http://localhost:11434/v1", cfg.base_url)
        self.assertEqual("local", cfg.api_key)
        self.assertEqual("llama3.1", cfg.model)
        self.assertTrue(cfg.explicit)

    def test_embedding_base_url_selects_openai_compatible(self):
        with patch.dict(
            os.environ,
            {
                "CORE_MEMORY_EMBEDDINGS_BASE_URL": "http://localhost:1234/v1",
                "CORE_MEMORY_EMBEDDINGS_MODEL": "text-embedding-local",
            },
            clear=True,
        ):
            cfg = resolve_embedding_config()
        self.assertEqual("openai-compatible", cfg.provider)
        self.assertEqual("openai-compatible", cfg.adapter)
        self.assertEqual("http://localhost:1234/v1", cfg.base_url)
        self.assertEqual("text-embedding-local", cfg.embedding_model)

    def test_google_and_anthropic_are_named_adapters_not_default_priority(self):
        with patch.dict(os.environ, {"CORE_MEMORY_CHAT_PROVIDER": "google", "GOOGLE_API_KEY": "g"}, clear=True):
            self.assertEqual("google", resolve_chat_config().adapter)
        with patch.dict(os.environ, {"CORE_MEMORY_CHAT_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "a"}, clear=True):
            self.assertEqual("anthropic", resolve_chat_config().adapter)


if __name__ == "__main__":
    unittest.main()
