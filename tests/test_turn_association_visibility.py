import tempfile
import unittest

from core_memory.runtime.engine import process_turn_finalized
from core_memory.persistence.store import MemoryStore


class TestTurnAssociationVisibility(unittest.TestCase):
    def test_turn_can_append_association_across_visible_session_beads(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(type="context", title="B1", summary=["x"], session_id="s1", source_turn_ids=["t0"])
            b2 = s.add_bead(type="context", title="B2", summary=["y"], session_id="s1", source_turn_ids=["t1"])

            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="link prior beads",
                assistant_final="linked",
                metadata={
                    "crawler_updates": {
                        "associations": [
                            {
                                "source_bead_id": b1,
                                "target_bead_id": b2,
                                "relationship": "supports",
                                "confidence": 0.9,
                                "reason_text": "session-visible link",
                                "rationale": "session-visible link",
                            }
                        ]
                    }
                },
            )

            self.assertTrue(out.get("ok"))
            auto_apply = ((out.get("crawler_handoff") or {}).get("auto_apply") or {})
            self.assertGreaterEqual(int(auto_apply.get("associations_appended") or 0), 1)

            turn_merge = ((out.get("crawler_handoff") or {}).get("turn_merge") or {})
            self.assertGreaterEqual(int(turn_merge.get("associations_appended") or 0), 1)

            idx = s._read_json(s.beads_dir / "index.json")
            rels = [a for a in (idx.get("associations") or []) if str(a.get("relationship") or "") == "supports"]
            self.assertGreaterEqual(len(rels), 1)


if __name__ == "__main__":
    unittest.main()
