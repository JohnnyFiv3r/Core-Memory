import tempfile
import unittest

from core_memory.runtime.worker import process_memory_event, SidecarPolicy
from core_memory.store import MemoryStore


class TestP13AuthorityEnforcement(unittest.TestCase):
    def test_store_add_bead_does_not_append_canonical_associations(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(
                type="context",
                title="decision alpha",
                summary=["promotion workflow"],
                tags=["promotion_workflow"],
                session_id="s1",
                source_turn_ids=["t1"],
            )
            s.add_bead(
                type="context",
                title="decision alpha followup",
                summary=["promotion workflow"],
                tags=["promotion_workflow"],
                session_id="s1",
                source_turn_ids=["t2"],
            )

            idx = s._read_json(s.beads_dir / "index.json")
            self.assertEqual([], idx.get("associations") or [])

    def test_worker_does_not_mutate_canonical_promotion_state(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b = s.add_bead(type="context", title="seed", summary=["x"], session_id="s1", source_turn_ids=["t1"])

            payload = {
                "envelope": {
                    "session_id": "s1",
                    "turn_id": "t2",
                    "user_query": "remember this important decision",
                    "assistant_final": "Important decision: always do X, confirmed outcome",
                    "window_bead_ids": [b],
                    "envelope_hash": "h2",
                }
            }
            delta = process_memory_event(td, payload, policy=SidecarPolicy())
            self.assertEqual(0, len(delta.get("created") or []))
            self.assertEqual(0, len(delta.get("creation_candidates") or []))
            self.assertEqual(0, len(delta.get("promoted") or []))

            idx = s._read_json(s.beads_dir / "index.json")
            seed = (idx.get("beads") or {}).get(b) or {}
            self.assertEqual("open", seed.get("status"))
            self.assertFalse(bool(seed.get("promotion_marked")))


if __name__ == "__main__":
    unittest.main()
