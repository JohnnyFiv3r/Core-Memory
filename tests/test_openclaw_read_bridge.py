import json
import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.integrations.openclaw_read_bridge import dispatch


class TestOpenClawReadBridge(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.root = str(Path(self.td) / "memory")
        self.store = MemoryStore(self.root)
        self.store.add_bead(
            type="decision",
            title="Use PostgreSQL for primary storage",
            summary=["Supports JSONB", "Better CTE support"],
            tags=["database", "postgresql"],
            session_id="s1",
            source_turn_ids=["t1"],
        )

    def test_search_with_query_shorthand(self):
        result = dispatch({"action": "search", "query": "PostgreSQL", "root": self.root})
        self.assertIn("results", result)

    def test_search_with_form_submission(self):
        result = dispatch({
            "action": "search",
            "root": self.root,
            "form_submission": {"query_text": "database", "k": 3},
        })
        self.assertIn("results", result)

    def test_search_with_request_payload(self):
        result = dispatch(
            {
                "action": "search",
                "root": self.root,
                "request": {"query_text": "database", "k": 3},
            }
        )
        self.assertIn("results", result)

    def test_search_missing_query(self):
        result = dispatch({"action": "search", "root": self.root})
        self.assertFalse(result.get("ok", True))
        self.assertIn("missing", result.get("error", ""))

    def test_trace(self):
        result = dispatch({"action": "trace", "query": "why PostgreSQL?", "root": self.root})
        self.assertIn("ok", result)

    def test_trace_with_anchor_ids_only(self):
        idx = self.store._read_json(Path(self.root) / ".beads" / "index.json")
        bead_ids = list((idx.get("beads") or {}).keys())
        result = dispatch({"action": "trace", "root": self.root, "anchor_ids": bead_ids[:1]})
        self.assertTrue(result.get("ok"))
        self.assertTrue(bool(result.get("anchors") or []))

    def test_trace_missing_query(self):
        result = dispatch({"action": "trace", "root": self.root})
        self.assertFalse(result.get("ok", True))

    def test_continuity_json(self):
        result = dispatch({"action": "continuity", "root": self.root})
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("format"), "json")
        self.assertIn("authority", result)

    def test_continuity_text(self):
        result = dispatch({"action": "continuity", "root": self.root, "format": "text"})
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("format"), "text")
        self.assertIn("text", result)

    def test_session_start_boundary_action(self):
        result = dispatch({"action": "session_start", "root": self.root, "session_id": "s1"})
        self.assertTrue(result.get("ok"))
        self.assertIn("bead_id", result)

    def test_execute_with_query(self):
        result = dispatch({"action": "execute", "query": "PostgreSQL", "root": self.root})
        self.assertIn("ok", result)

    def test_execute_missing_request(self):
        result = dispatch({"action": "execute", "root": self.root})
        self.assertFalse(result.get("ok", True))

    def test_removed_actions_are_unknown(self):
        result = dispatch({"action": "reason", "root": self.root})
        self.assertFalse(result.get("ok", True))
        self.assertIn("unknown_action", result.get("error", ""))

        result2 = dispatch({"action": "search-form", "root": self.root})
        self.assertFalse(result2.get("ok", True))
        self.assertIn("unknown_action", result2.get("error", ""))

    def test_unknown_action(self):
        result = dispatch({"action": "bogus", "root": self.root})
        self.assertFalse(result.get("ok", True))
        self.assertIn("unknown_action", result.get("error", ""))
        self.assertIn("supported", result)

    def test_empty_action(self):
        result = dispatch({"root": self.root})
        self.assertFalse(result.get("ok", True))


if __name__ == "__main__":
    unittest.main()
