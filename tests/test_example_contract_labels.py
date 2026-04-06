from __future__ import annotations

import re
import unittest
from pathlib import Path


class TestExampleContractLabels(unittest.TestCase):
    def test_all_examples_have_contract_and_audience_labels(self):
        repo = Path(__file__).resolve().parents[1]
        examples_dir = repo / "examples"
        files = sorted(examples_dir.glob("*.py"))
        self.assertTrue(files, "No example files found")

        allowed_levels = {"Canonical", "Recommended", "Compatibility", "Experimental"}
        missing = []
        bad_level = []

        for path in files:
            text = path.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"Contract Level:\s*(.+)", text)
            if not m:
                missing.append(str(path.relative_to(repo)))
                continue
            level = m.group(1).strip()
            if level not in allowed_levels:
                bad_level.append((str(path.relative_to(repo)), level))
            if not re.search(r"Audience:\s*(.+)", text):
                missing.append(str(path.relative_to(repo)))

        self.assertEqual([], missing, f"Examples missing labels: {missing}")
        self.assertEqual([], bad_level, f"Examples with invalid Contract Level labels: {bad_level}")


if __name__ == "__main__":
    unittest.main()
