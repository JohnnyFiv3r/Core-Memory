import unittest

from core_memory.association import run_association_pass


class TestAssociationPassStrengthened(unittest.TestCase):
    def test_session_relative_weighting_prefers_same_session(self):
        idx = {
            "beads": {
                "old_same": {
                    "id": "old_same",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "title": "promotion decision",
                    "summary": ["candidate only"],
                    "tags": ["promotion_workflow"],
                    "session_id": "s1",
                },
                "old_other": {
                    "id": "old_other",
                    "created_at": "2026-01-02T00:00:00+00:00",
                    "title": "promotion decision",
                    "summary": ["candidate only"],
                    "tags": ["promotion_workflow"],
                    "session_id": "s2",
                },
            }
        }
        bead = {
            "id": "new",
            "created_at": "2026-01-03T00:00:00+00:00",
            "title": "promotion decision",
            "summary": ["candidate only"],
            "tags": ["promotion_workflow"],
            "session_id": "s1",
        }

        out = run_association_pass(idx, bead, max_lookback=10, top_k=2)
        self.assertEqual("old_same", out[0]["other_id"])

    def test_causal_typing_can_emit_supports(self):
        idx = {
            "beads": {
                "b1": {
                    "id": "b1",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "title": "because promotion inflation caused compaction issues",
                    "summary": ["led to gating"],
                    "tags": ["promotion_workflow"],
                    "session_id": "s1",
                }
            }
        }
        bead = {
            "id": "b2",
            "created_at": "2026-01-02T00:00:00+00:00",
            "title": "because we blocked broad promotion",
            "summary": ["therefore candidate-only"],
            "tags": ["promotion_workflow"],
            "session_id": "s1",
        }

        out = run_association_pass(idx, bead, max_lookback=10, top_k=1)
        self.assertEqual("supports", out[0]["relationship"])


if __name__ == "__main__":
    unittest.main()
