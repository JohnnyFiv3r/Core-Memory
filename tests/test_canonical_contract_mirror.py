from pathlib import Path
import unittest


class TestCanonicalContractMirror(unittest.TestCase):
    def test_runtime_prompt_mirrors_canonical_openclaw_contract(self):
        repo = Path(__file__).resolve().parents[1]
        runtime_prompt = repo / "AGENT_INSTRUCTIONS.md"
        canonical = repo / "docs" / "integrations" / "openclaw" / "canonical_contract.md"

        self.assertTrue(runtime_prompt.exists())
        self.assertTrue(canonical.exists())
        self.assertEqual(canonical.read_text(encoding="utf-8"), runtime_prompt.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
