import pathlib
import tomllib
import unittest
from unittest.mock import patch

from core_memory.provider_config import provider_extra_hint, resolve_chat_config


class TestProviderNeutralDependencies(unittest.TestCase):
    def test_base_package_stays_minimal_and_hosted_sdks_are_extras(self):
        data = tomllib.loads(pathlib.Path("pyproject.toml").read_text())
        deps = data["project"].get("dependencies") or []
        self.assertEqual(["pyyaml>=6.0"], deps)
        extras = data["project"]["optional-dependencies"]
        self.assertEqual(["openai"], extras["openai"])
        self.assertEqual(["anthropic"], extras["anthropic"])
        self.assertIn("google-genai", extras["google"])
        self.assertIn("numpy", extras["semantic"])
        self.assertIn("faiss-cpu", extras["semantic"])
        self.assertIn("mcp>=1.27.1,<2", extras["mcp"])

    def test_selected_provider_missing_extra_hint_is_actionable(self):
        self.assertIn("core-memory[anthropic]", provider_extra_hint("anthropic"))
        self.assertIn("core-memory[google]", provider_extra_hint("google"))
        self.assertIn("core-memory[openai]", provider_extra_hint("openrouter"))

    def test_legacy_key_detection_uses_provider_neutral_config_not_required_sdk_import(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "a"}, clear=True):
            cfg = resolve_chat_config()
        self.assertEqual("anthropic", cfg.adapter)
        self.assertFalse(cfg.explicit)
        self.assertEqual("a", cfg.api_key)


if __name__ == "__main__":
    unittest.main()
