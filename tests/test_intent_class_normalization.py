import unittest

from core_memory.retrieval.tools.memory_reason import _intent_class_from_query
from core_memory.retrieval.query_norm import classify_intent, normalize_query


class TestIntentClassNormalization(unittest.TestCase):
    def test_intent_class_rules(self):
        self.assertEqual("causal", _intent_class_from_query("why did this happen"))
        self.assertEqual("what_changed", _intent_class_from_query("what changed in policy"))
        self.assertEqual("when", _intent_class_from_query("when was this decided"))
        self.assertEqual("remember", _intent_class_from_query("remember this"))

    def test_authoritative_causal_flag_matches_intent_class(self):
        out = classify_intent("what caused the promotion inflation episode")
        self.assertEqual("causal", out.get("intent_class"))
        self.assertTrue(out.get("causal_intent"))

    def test_query_normalizer_outputs_tokens_and_phrases(self):
        q = normalize_query('What changed in "structural sync" pipeline updates?')
        self.assertIn("raw_normalized", q)
        self.assertTrue(isinstance(q.get("tokens"), list))
        self.assertTrue(isinstance(q.get("phrases"), list))


if __name__ == "__main__":
    unittest.main()
