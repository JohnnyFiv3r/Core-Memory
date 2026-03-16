import unittest

from core_memory.retrieval.pipeline.execute import evaluate_confidence_next


class TestMemoryExecuteConfidenceEdgeCases(unittest.TestCase):
    def test_causal_ungrounded_prefers_ask_clarifying_over_broaden(self):
        conf, nxt, diag = evaluate_confidence_next(
            intent="causal",
            results=[{"bead_id": "b1", "score": 0.2}],
            chains=[],
            snapped={"intent": "causal", "topic_keys": []},
            beads={},
            warnings=[],
        )
        self.assertIn(conf, {"low", "medium"})
        self.assertEqual("ask_clarifying", nxt)
        self.assertIn("anchor_present", diag)

    def test_non_benign_warning_blocks_high_confidence(self):
        conf, nxt, _ = evaluate_confidence_next(
            intent="remember",
            results=[{"bead_id": "b1", "score": 0.9}, {"bead_id": "b2", "score": 0.7}],
            chains=[],
            snapped={"incident_id": "inc_1", "topic_keys": []},
            beads={"b1": {"incident_id": "inc_1"}},
            warnings=["no_strong_anchor_match_free_text_mode"],
        )
        self.assertEqual("medium", conf)
        self.assertEqual("answer", nxt)


if __name__ == "__main__":
    unittest.main()
