import unittest

from core_memory.tools.memory_reason import _intent_class_from_query


class TestIntentClassNormalization(unittest.TestCase):
    def test_intent_class_rules(self):
        self.assertEqual("causal", _intent_class_from_query("why did this happen"))
        self.assertEqual("what_changed", _intent_class_from_query("what changed in policy"))
        self.assertEqual("when", _intent_class_from_query("when was this decided"))
        self.assertEqual("remember", _intent_class_from_query("remember this"))


if __name__ == "__main__":
    unittest.main()
