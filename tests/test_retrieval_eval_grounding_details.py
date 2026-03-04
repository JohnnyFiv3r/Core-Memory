import json
import tempfile
import unittest
from pathlib import Path

from eval.retrieval_eval import _causal_grounding_components


class TestRetrievalEvalGroundingDetails(unittest.TestCase):
    def test_grounding_components_shape(self):
        out = _causal_grounding_components(
            {
                "chains": [{"edges": [{"src": "a", "dst": "b", "rel": "supports"}]}],
                "citations": [{"type": "decision"}, {"type": "evidence"}],
            }
        )
        self.assertIn("grounded", out)
        self.assertIn("has_decision_like", out)
        self.assertIn("has_evidence_like", out)
        self.assertIn("has_structural", out)
        self.assertTrue(out["grounded"])


if __name__ == "__main__":
    unittest.main()
