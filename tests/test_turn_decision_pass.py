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

            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="We decided to keep a bridge layer",
                assistant_final="Decision confirmed with evidence and references.",
            )
            self.assertTrue(out.get("ok"))
            handoff = (out.get("crawler_handoff") or {})
            decision = handoff.get("decision_pass") or {}
            self.assertTrue(decision.get("ok"))
            self.assertGreaterEqual(int((decision.get("counts") or {}).get("evaluated", 0)), 1)


if __name__ == "__main__":
    unittest.main()
