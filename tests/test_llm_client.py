from __future__ import annotations

import unittest
from unittest.mock import patch

from core_memory.llm_client import chat_complete
from core_memory.provider_config import ProviderConfig


class TestLlmClientOpenAICompatiblePayload(unittest.TestCase):
    def test_gpt5_models_use_completion_tokens_and_default_temperature(self):
        cfg = ProviderConfig(
            kind="chat",
            provider="openai",
            base_url="https://example.test/v1",
            api_key="test",
            model="gpt-5.2",
            source="unit",
            explicit=True,
        )

        with patch(
            "core_memory.llm_client._post_json",
            return_value={"choices": [{"message": {"content": "ok"}}]},
        ) as post:
            self.assertEqual("ok", chat_complete("hello", config=cfg, max_tokens=123, temperature=0))

        payload = post.call_args.args[1]
        self.assertEqual("gpt-5.2", payload["model"])
        self.assertEqual(123, payload["max_completion_tokens"])
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("temperature", payload)

    def test_legacy_chat_models_keep_max_tokens_and_temperature(self):
        cfg = ProviderConfig(
            kind="chat",
            provider="openai",
            base_url="https://example.test/v1",
            api_key="test",
            model="gpt-4o-mini",
            source="unit",
            explicit=True,
        )

        with patch(
            "core_memory.llm_client._post_json",
            return_value={"choices": [{"message": {"content": "ok"}}]},
        ) as post:
            self.assertEqual("ok", chat_complete("hello", config=cfg, max_tokens=456, temperature=0.2))

        payload = post.call_args.args[1]
        self.assertEqual("gpt-4o-mini", payload["model"])
        self.assertEqual(456, payload["max_tokens"])
        self.assertEqual(0.2, payload["temperature"])
        self.assertNotIn("max_completion_tokens", payload)

    def test_o_series_models_use_completion_tokens(self):
        cfg = ProviderConfig(
            kind="chat",
            provider="openai",
            base_url="https://example.test/v1",
            api_key="test",
            model="o4-mini",
            source="unit",
            explicit=True,
        )

        with patch(
            "core_memory.llm_client._post_json",
            return_value={"choices": [{"message": {"content": "ok"}}]},
        ) as post:
            self.assertEqual("ok", chat_complete("hello", config=cfg, max_tokens=789, temperature=0))

        payload = post.call_args.args[1]
        self.assertEqual("o4-mini", payload["model"])
        self.assertEqual(789, payload["max_completion_tokens"])
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("temperature", payload)


if __name__ == "__main__":
    unittest.main()
