import tempfile
import unittest

from core_memory.memory_engine import crawler_turn_context, apply_crawler_turn_updates
from core_memory.store import MemoryStore


class TestAssociationCrawlerContract(unittest.TestCase):
    def test_context_and_append_only_updates(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(type="context", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b2 = s.add_bead(type="context", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"])

            ctx = crawler_turn_context(root=td, session_id="s1")
            self.assertEqual("crawler_turn_context", (ctx.get("engine") or {}).get("entry"))
            self.assertGreaterEqual(len(ctx.get("beads") or []), 2)

            out = apply_crawler_turn_updates(
                root=td,
                session_id="s1",
                updates={
                    "promotions": [b1],
                    "associations": [
                        {
                            "source_bead_id": b1,
                            "target_bead_id": b2,
                            "relationship": "supports",
                        }
                    ],
                },
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual("apply_crawler_turn_updates", (out.get("engine") or {}).get("entry"))
            self.assertEqual(1, out.get("promotions_marked"))
            self.assertEqual(1, out.get("associations_appended"))

            idx = s._read_json(s.beads_dir / "index.json")
            self.assertTrue((idx.get("beads", {}).get(b1) or {}).get("promotion_marked"))
            self.assertTrue(any(a.get("source_bead") == b1 and a.get("target_bead") == b2 for a in idx.get("associations", [])))


if __name__ == "__main__":
    unittest.main()
