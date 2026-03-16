import unittest

from core_memory.retrieval.tools.memory_reason import _chain_why_priority


class TestChainPriorityWhy(unittest.TestCase):
    def test_structural_decision_evidence_priority(self):
        weak = {"score": 0.4, "edges": [], "beads": [{"type": "context"}]}
        strong = {
            "score": 0.2,
            "edges": [{"class": "structural", "rel": "supports"}],
            "beads": [{"type": "decision"}, {"type": "evidence"}],
        }
        self.assertGreater(_chain_why_priority(strong), _chain_why_priority(weak))


if __name__ == "__main__":
    unittest.main()
