import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import json


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

    def test_writes_claims_only_to_canonical_turn_bead(self):
        from core_memory.claim.turn_integration import extract_and_attach_claims
        from core_memory.persistence.store_claim_ops import read_claims_for_bead

        with tempfile.TemporaryDirectory() as td:
            idx_path = Path(td) / ".beads" / "index.json"
            idx_path.parent.mkdir(parents=True, exist_ok=True)
            idx = {
                "beads": {
                    "bead-canon": {
                        "id": "bead-canon",
                        "session_id": "s1",
                        "source_turn_ids": ["t1"],
                        "tags": ["turn_finalized", "seeded_by_engine"],
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                    "bead-other": {
                        "id": "bead-other",
                        "session_id": "s1",
                        "source_turn_ids": ["t1"],
                        "tags": ["crawler_reviewed"],
                        "created_at": "2026-01-01T00:00:01Z",
                    },
                },
                "associations": [],
            }
            idx_path.write_text(json.dumps(idx), encoding="utf-8")

            with patch("core_memory.claim.turn_integration.claim_layer_enabled", return_value=True), patch(
                "core_memory.claim.turn_integration.claim_extraction_mode", return_value="heuristic"
            ):
                out = extract_and_attach_claims(
                    td,
                    "s1",
                    "t1",
                    ["bead-canon", "bead-other"],
                    {"user_query": "I prefer tea", "assistant_final": ""},
                )

            self.assertEqual("bead-canon", out.get("canonical_bead_id"))
            self.assertGreaterEqual(len(read_claims_for_bead(td, "bead-canon")), 1)
            self.assertEqual([], read_claims_for_bead(td, "bead-other"))


if __name__ == "__main__":
    unittest.main()
