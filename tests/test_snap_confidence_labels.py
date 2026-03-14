import unittest

from core_memory.retrieval.pipeline.snap import snap_form


class TestSnapConfidenceLabels(unittest.TestCase):
    def test_snap_decisions_include_labels(self):
        catalog = {
            "incident_ids": ["promotion_inflation_2026q1"],
            "topic_keys": ["promotion_workflow"],
            "bead_types": ["decision"],
            "relation_types": ["supports"],
        }
        out = snap_form({
            "intent": "causal",
            "query_text": "why",
            "incident_id": "promotion inflation 2026q1",
            "topic_keys": ["promotion workflow"],
            "relation_types": ["support"],
            "bead_types": ["decision"],
        }, catalog)
        d = out.get("decisions") or {}
        self.assertIn("incident_id", d)
        self.assertIn("confidence_label", d.get("incident_id") or {})
        self.assertTrue(isinstance(d.get("topic_keys"), list))


if __name__ == "__main__":
    unittest.main()
