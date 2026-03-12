import tempfile
import unittest

from core_memory.memory_engine import process_turn_finalized
from core_memory.store import MemoryStore


class TestTurnDecisionPass(unittest.TestCase):
    def test_turn_runs_visible_bead_decision_pass(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            # Seed one prior visible bead in same session with weak signal -> candidate/null path.
            store.add_bead(
                type="context",
                title="Prior session note",
                summary=["short"],
                detail="tiny",
                session_id="s1",
                source_turn_ids=["t0"],
                status="open",
            )

            out1 = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="We decided to keep a bridge layer",
                assistant_final="Decision confirmed with evidence and references.",
            )
            self.assertTrue(out1.get("ok"))
            d1 = ((out1.get("crawler_handoff") or {}).get("decision_pass") or {})
            self.assertTrue(d1.get("ok"))
            self.assertGreaterEqual(int((d1.get("counts") or {}).get("evaluated", 0)), 1)

            out2 = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t2",
                user_query="Second turn should see prior beads",
                assistant_final="Still consistent.",
            )
            self.assertTrue(out2.get("ok"))
            d2 = ((out2.get("crawler_handoff") or {}).get("decision_pass") or {})
            self.assertTrue(d2.get("ok"))
            self.assertGreaterEqual(int((d2.get("counts") or {}).get("evaluated", 0)), 2)


if __name__ == "__main__":
    unittest.main()
