import unittest

from core_memory.tools.memory_reason import _causal_intent, _grounding_signal


class TestCausalRetryGuard(unittest.TestCase):
    def test_causal_intent_detection(self):
        self.assertTrue(_causal_intent("why did we decide this"))
        self.assertTrue(_causal_intent("what happened there"))
        self.assertFalse(_causal_intent("remember this"))

    def test_grounding_signal_prefers_structural_and_evidence(self):
        low = {
            "chains": [{"edges": []}],
            "citations": [{"type": "context"}],
        }
        hi = {
            "chains": [{"edges": [{"src": "a", "dst": "b", "rel": "supports"}]}],
            "citations": [{"type": "decision"}, {"type": "evidence"}],
        }
        self.assertGreater(_grounding_signal(hi), _grounding_signal(low))


if __name__ == "__main__":
    unittest.main()
