import tempfile
import unittest

from core_memory.runtime.engine import process_turn_finalized
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

    def test_custom_metadata_updates_still_create_turn_bead(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            b1 = store.add_bead(type="context", title="Seed", summary=["x"], session_id="s2", source_turn_ids=["t0"])

            out1 = process_turn_finalized(
                root=td,
                session_id="s2",
                turn_id="t1",
                user_query="Link only metadata",
                assistant_final="No explicit create in metadata",
                metadata={
                    "crawler_updates": {
                        "associations": [
                            {
                                "source_bead_id": b1,
                                "target_bead_id": b1,
                                "relationship": "supports",
                            }
                        ]
                    }
                },
            )
            self.assertTrue(out1.get("ok"))

            out2 = process_turn_finalized(
                root=td,
                session_id="s2",
                turn_id="t2",
                user_query="Second turn",
                assistant_final="Should evaluate both seed and t1 bead",
            )
            self.assertTrue(out2.get("ok"))
            d2 = ((out2.get("crawler_handoff") or {}).get("decision_pass") or {})
            self.assertGreaterEqual(int((d2.get("counts") or {}).get("evaluated", 0)), 2)


if __name__ == "__main__":
    unittest.main()
