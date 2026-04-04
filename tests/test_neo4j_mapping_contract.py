import unittest

from core_memory.integrations.neo4j.mapper import association_to_edge, bead_to_node


class TestNeo4jMappingContract(unittest.TestCase):
    def test_bead_to_node_includes_required_properties_and_labels(self):
        bead = {
            "id": "bead-1",
            "type": "decision",
            "title": "Use queue",
            "status": "open",
            "session_id": "s1",
            "scope": "project",
            "authority": "agent",
            "created_at": "2026-04-04T00:00:00+00:00",
            "updated_at": "2026-04-04T01:00:00+00:00",
            "retrieval_eligible": True,
            "promotion_marked": False,
            "confidence": 0.88,
            "tags": ["infra"],
            "topics": ["queue"],
            "entities": ["redis"],
            "source_turn_ids": ["t1"],
            "summary": ["queue decision"],
            "detail": "d",
            "because": ["b"],
            "retrieval_title": "rt",
            "retrieval_facts": ["f1"],
            "incident_id": "inc-1",
            "validity": "active",
            "effective_from": "2026-04-04",
            "effective_to": "",
        }
        out = bead_to_node(bead)
        self.assertIn("Bead", out.get("labels") or [])
        self.assertIn("Decision", out.get("labels") or [])
        props = out.get("properties") or {}
        required = [
            "bead_id",
            "type",
            "title",
            "status",
            "session_id",
            "scope",
            "authority",
            "created_at",
            "updated_at",
            "retrieval_eligible",
            "promotion_marked",
            "confidence",
            "tags",
            "topics",
            "entities",
            "source_turn_ids",
        ]
        for key in required:
            self.assertIn(key, props)
        self.assertEqual("bead-1", props.get("bead_id"))
        self.assertEqual(["queue decision"], props.get("summary"))

    def test_session_start_maps_to_sessionstart_label(self):
        out = bead_to_node({"id": "b", "type": "session_start", "title": "Session start", "tags": ["session_start"]})
        self.assertIn("SessionStart", out.get("labels") or [])
        self.assertEqual("session_start", (out.get("properties") or {}).get("type"))

    def test_bead_string_fields_are_normalized_to_lists(self):
        out = bead_to_node(
            {
                "id": "b2",
                "type": "lesson",
                "summary": "single summary string",
                "tags": "tag1",
                "source_turn_ids": "t1",
            }
        )
        props = out.get("properties") or {}
        self.assertEqual(["single summary string"], props.get("summary"))
        self.assertEqual(["tag1"], props.get("tags"))
        self.assertEqual(["t1"], props.get("source_turn_ids"))

    def test_association_uses_explicit_association_id_when_present(self):
        edge = association_to_edge(
            {
                "id": "assoc-123",
                "source_bead": "a",
                "target_bead": "b",
                "relationship": "supports",
            }
        )
        props = edge.get("properties") or {}
        self.assertEqual("assoc-123", props.get("association_id"))
        self.assertEqual("supports", props.get("relationship"))

    def test_association_fallback_key_is_stable(self):
        assoc = {"source_bead": "a", "target_bead": "b", "relationship": "Supports "}
        e1 = association_to_edge(assoc)
        e2 = association_to_edge(assoc)
        p1 = e1.get("properties") or {}
        p2 = e2.get("properties") or {}
        self.assertEqual(p1.get("association_id"), p2.get("association_id"))
        self.assertEqual(p1.get("dedupe_key"), p2.get("dedupe_key"))
        self.assertEqual("supports", p1.get("relationship"))


if __name__ == "__main__":
    unittest.main()
