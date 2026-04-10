import os
import unittest
from unittest.mock import patch


class TestClaimFlags(unittest.TestCase):
    def test_claim_layer_disabled_by_default(self):
        from core_memory.integrations.openclaw_flags import claim_layer_enabled

        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(claim_layer_enabled())

    def test_claim_layer_enabled_with_env(self):
        from core_memory.integrations.openclaw_flags import claim_layer_enabled

        with patch.dict(os.environ, {"CORE_MEMORY_CLAIM_LAYER": "1"}, clear=False):
            self.assertTrue(claim_layer_enabled())

    def test_claim_extraction_mode_default_off(self):
        from core_memory.integrations.openclaw_flags import claim_extraction_mode

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual("off", claim_extraction_mode())

    def test_claim_extraction_mode_heuristic(self):
        from core_memory.integrations.openclaw_flags import claim_extraction_mode

        with patch.dict(os.environ, {"CORE_MEMORY_CLAIM_EXTRACTION_MODE": "heuristic"}, clear=False):
            self.assertEqual("heuristic", claim_extraction_mode())

    def test_claim_extraction_mode_invalid_returns_off(self):
        from core_memory.integrations.openclaw_flags import claim_extraction_mode

        with patch.dict(os.environ, {"CORE_MEMORY_CLAIM_EXTRACTION_MODE": "invalid_value"}, clear=False):
            self.assertEqual("off", claim_extraction_mode())

    def test_claim_resolution_disabled_by_default(self):
        from core_memory.integrations.openclaw_flags import claim_resolution_enabled

        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(claim_resolution_enabled())

    def test_claim_retrieval_boost_disabled_by_default(self):
        from core_memory.integrations.openclaw_flags import claim_retrieval_boost_enabled

        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(claim_retrieval_boost_enabled())


if __name__ == "__main__":
    unittest.main()
