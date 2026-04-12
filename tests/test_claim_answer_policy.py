import unittest

from core_memory.claim.answer_policy import ANSWER_OUTCOMES, decide_answer_outcome, score_answer
from core_memory.claim.answer_signals import compute_answer_signals


def make_state(active=0, conflict=0):
    slots = {}
    for i in range(active):
        slots[f"user:slot{i}"] = {"status": "active", "current_claim": {"id": f"c{i}", "confidence": 0.8}}
    for i in range(conflict):
        slots[f"user:conflict{i}"] = {"status": "conflict", "current_claim": None}
    return {"slots": slots, "total_slots": active + conflict, "active_slots": active, "conflict_slots": conflict}


def make_results(n=3, score=0.8):
    return [{"id": f"r{i}", "score": score} for i in range(n)]


class TestClaimAnswerPolicy(unittest.TestCase):
    def test_abstain_when_no_anchor_no_evidence(self):
        self.assertEqual("abstain", decide_answer_outcome([], None, "what is my preference?"))

    def test_answer_current_high_confidence(self):
        self.assertEqual("answer_current", decide_answer_outcome(make_results(5), make_state(active=2), "what do I prefer?"))

    def test_historical_cue_without_temporal_alignment_stays_partial(self):
        self.assertEqual("answer_partial", decide_answer_outcome([], make_state(active=1), "what did I used to prefer?"))

    def test_answer_historical_with_explicit_alignment(self):
        results = [
            {
                "score": 0.7,
                "feature_scores": {"temporal_fit": 1.0},
            }
        ]
        self.assertEqual(
            "answer_historical",
            decide_answer_outcome(results, make_state(active=1), "what was this as of last week", as_of="2026-01-01T00:00:00Z"),
        )

    def test_answer_partial_some_evidence(self):
        self.assertEqual("answer_partial", decide_answer_outcome(make_results(2, score=0.5), None, "anything about preferences?"))

    def test_answer_partial_on_conflict(self):
        self.assertEqual("answer_partial", decide_answer_outcome(make_results(3), make_state(active=1, conflict=2), "what do I prefer?"))

    def test_abstain_is_rare(self):
        self.assertNotEqual("abstain", decide_answer_outcome(make_results(1, score=0.3), None, "query"))

    def test_all_outcomes_defined(self):
        for outcome in ["answer_current", "answer_historical", "answer_partial", "abstain"]:
            self.assertIn(outcome, ANSWER_OUTCOMES)

    def test_score_answer_returns_outcome_and_signals(self):
        result = score_answer(make_results(3), make_state(active=2), "query")
        self.assertIn("outcome", result)
        self.assertIn("signals", result)
        self.assertIn("anchor_confidence", result["signals"])
        self.assertIn("evidence_sufficiency", result["signals"])
        self.assertIn("conflict_penalty", result["signals"])

    def test_signals_valid_range(self):
        signals = compute_answer_signals(make_results(3), make_state(active=1), "query")
        for key, val in signals.items():
            self.assertGreaterEqual(val, 0.0, key)
            self.assertLessEqual(val, 1.0, key)


if __name__ == "__main__":
    unittest.main()
