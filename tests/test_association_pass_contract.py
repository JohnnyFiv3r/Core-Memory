import copy
import unittest

from core_memory.association import run_association_pass


class TestAssociationPassContract(unittest.TestCase):
    def test_contract_shape_and_determinism(self):
        idx = {
            "beads": {
                "b1": {"id": "b1", "created_at": "2026-01-01T00:00:00+00:00", "title": "A", "summary": ["alpha"], "tags": ["x"], "session_id": "s"},
                "b2": {"id": "b2", "created_at": "2026-01-02T00:00:00+00:00", "title": "B", "summary": ["beta"], "tags": ["x"], "session_id": "s"},
            }
        }
        bead = {"id": "b3", "title": "C", "summary": ["alpha beta"], "tags": ["x"], "session_id": "s", "created_at": "2026-01-03T00:00:00+00:00"}

        a = run_association_pass(idx, bead, max_lookback=10, top_k=3)
        b = run_association_pass(idx, bead, max_lookback=10, top_k=3)
        self.assertEqual(a, b)
        self.assertTrue(all(isinstance(x, dict) for x in a))
        for x in a:
            self.assertIn("other_id", x)
            self.assertIn("relationship", x)
            self.assertIn("score", x)

    def test_non_destructive(self):
        idx = {
            "beads": {
                "b1": {"id": "b1", "created_at": "2026-01-01T00:00:00+00:00", "title": "A", "summary": ["alpha"], "tags": ["x"], "session_id": "s"},
            }
        }
        bead = {"id": "b2", "title": "B", "summary": ["alpha"], "tags": ["x"], "session_id": "s", "created_at": "2026-01-02T00:00:00+00:00"}
        idx_before = copy.deepcopy(idx)
        bead_before = copy.deepcopy(bead)

        _ = run_association_pass(idx, bead)

        self.assertEqual(idx_before, idx)
        self.assertEqual(bead_before, bead)


if __name__ == "__main__":
    unittest.main()
