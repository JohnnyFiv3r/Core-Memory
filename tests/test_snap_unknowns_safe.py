import unittest

from core_memory.memory_skill.snap import snap_form


class TestSnapUnknownsSafe(unittest.TestCase):
    def test_unknown_values_do_not_crash_or_hallucinate(self):
        catalog = {
            "incident_ids": ["a"],
            "topic_keys": ["x"],
            "bead_types": ["decision"],
            "relation_types": ["supports"],
        }
        sub = {
            "intent": "remember",
            "query_text": "q",
            "incident_id": "totally unknown incident",
            "topic_keys": ["unknown topic"],
            "bead_types": ["decision", "madeup"],
        }
        out = snap_form(sub, catalog)
        snapped = out.get("snapped") or {}
        self.assertIn(snapped.get("incident_id"), [None, "a"])
        self.assertEqual(["decision"], snapped.get("bead_types"))


if __name__ == "__main__":
    unittest.main()
