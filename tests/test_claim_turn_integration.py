import tempfile
import unittest
from unittest.mock import patch


class TestClaimTurnIntegration(unittest.TestCase):
    def test_extract_and_attach_claims_flag_off(self):
        from core_memory.claim.turn_integration import extract_and_attach_claims

        with patch("core_memory.claim.turn_integration.claim_layer_enabled", return_value=False):
            result = extract_and_attach_claims("/tmp", "s1", "t1", ["b1"], {"user_query": "I love jazz"})
        self.assertEqual(0, result["claims_extracted"])
        self.assertEqual(0, result["claims_written"])

    def test_extract_and_attach_claims_mode_off(self):
        from core_memory.claim.turn_integration import extract_and_attach_claims

        with patch("core_memory.claim.turn_integration.claim_layer_enabled", return_value=True), patch(
            "core_memory.claim.turn_integration.claim_extraction_mode", return_value="off"
        ):
            result = extract_and_attach_claims("/tmp", "s1", "t1", ["b1"], {"user_query": "I love jazz"})
        self.assertEqual(0, result["claims_extracted"])

    def test_extract_and_attach_claims_heuristic(self):
        from core_memory.claim.turn_integration import extract_and_attach_claims

        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.claim.turn_integration.claim_layer_enabled", return_value=True
        ), patch("core_memory.claim.turn_integration.claim_extraction_mode", return_value="heuristic"):
            result = extract_and_attach_claims(
                td,
                "s1",
                "t1",
                ["bead1"],
                {"user_query": "I prefer Python over Java", "assistant_final": ""},
            )
        self.assertIn("claims_extracted", result)
        self.assertIn("claims_written", result)
        self.assertIsInstance(result["bead_ids"], list)

    def test_extract_and_attach_empty_query(self):
        from core_memory.claim.turn_integration import extract_and_attach_claims

        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.claim.turn_integration.claim_layer_enabled", return_value=True
        ), patch("core_memory.claim.turn_integration.claim_extraction_mode", return_value="heuristic"):
            result = extract_and_attach_claims(td, "s1", "t1", [], {})
        self.assertEqual(0, result["claims_written"])


if __name__ == "__main__":
    unittest.main()
