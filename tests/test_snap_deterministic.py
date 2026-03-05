import unittest

from core_memory.memory_skill.snap import snap_form


class TestSnapDeterministic(unittest.TestCase):
    def test_same_input_same_snap(self):
        catalog = {
            "incident_ids": ["promotion_inflation_2026q1"],
            "topic_keys": ["promotion_workflow", "graph_archive_retrieval"],
            "bead_types": ["decision", "evidence"],
            "relation_types": ["supports", "derived_from"],
        }
        sub = {
            "intent": "causal",
            "query_text": "what happened",
            "incident_id": "promotion inflation",
            "topic_keys": ["promotion workflow"],
            "k": 12,
        }
        a = snap_form(sub, catalog)
        b = snap_form(sub, catalog)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
