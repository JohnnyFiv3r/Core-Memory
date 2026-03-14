import tempfile
import unittest
from pathlib import Path

from core_memory.policy.incidents import matched_incident_ids


class TestIncidentAliasMatch(unittest.TestCase):
    def test_alias_match(self):
        with tempfile.TemporaryDirectory() as td:
            ids = matched_incident_ids("remember promotion inflation episode", Path(td))
            self.assertIn("promotion_inflation_2026q1", ids)


if __name__ == "__main__":
    unittest.main()
