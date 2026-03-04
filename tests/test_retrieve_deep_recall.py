import tempfile
import unittest

from core_memory.store import MemoryStore


class TestRetrieveDeepRecall(unittest.TestCase):
    def test_deep_recall_uncompacts_top_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bead_id = store.add_bead(
                type="lesson",
                title="Redis pool exhaustion",
                summary=["Worker count exceeded pool"],
                detail="Detailed incident timeline and fix details",
                session_id="main",
            )
            store.compact(only_bead_ids=[bead_id])

            out = store.retrieve_with_context(
                query_text="remember redis pool exhaustion",
                deep_recall=True,
                max_uncompact_per_turn=1,
                limit=5,
            )

            self.assertTrue(out["deep_recall"]["enabled"])
            self.assertIn(bead_id, out["deep_recall"]["applied"])
            row = next(r for r in out["results"] if r["id"] == bead_id)
            self.assertTrue(row.get("detail_present"))
            self.assertIn("detail_preview", row)

    def test_memory_intent_auto_triggers_deep_recall(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bead_id = store.add_bead(
                type="decision",
                title="Increase Redis max connections",
                summary=["Raised pool to prevent timeouts"],
                detail="Decision rationale with measured before/after impact",
                session_id="main",
            )
            store.compact(only_bead_ids=[bead_id])

            out = store.retrieve_with_context(
                query_text="what did we decide about redis max connections",
                limit=5,
            )

            self.assertTrue(out["deep_recall"]["enabled"])
            self.assertIn(bead_id, out["deep_recall"]["attempted"])


if __name__ == "__main__":
    unittest.main()
