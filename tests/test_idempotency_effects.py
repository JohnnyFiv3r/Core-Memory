import tempfile
import unittest
from pathlib import Path

from core_memory.openclaw_integration import finalize_and_process_turn
from core_memory.store import MemoryStore


class TestIdempotencyEffects(unittest.TestCase):
    def test_same_turn_id_no_duplicate_effect(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            store = MemoryStore(root=root)

            turn_id = "idem-1"
            q = "important decision lesson root cause completed confirmed"
            a = "important decision lesson root cause completed confirmed"

            r1 = finalize_and_process_turn(
                root=root,
                session_id="main",
                turn_id=turn_id,
                transaction_id="tx1",
                trace_id="tr1",
                user_query=q,
                assistant_final=a,
                window_turn_ids=["w1"],
            )
            beads_after_1 = len(store._read_json(store.beads_dir / "index.json").get("beads", {}))

            r2 = finalize_and_process_turn(
                root=root,
                session_id="main",
                turn_id=turn_id,
                transaction_id="tx1",
                trace_id="tr1",
                user_query=q,
                assistant_final=a,
                window_turn_ids=["w1"],
            )
            beads_after_2 = len(store._read_json(store.beads_dir / "index.json").get("beads", {}))

            self.assertTrue(r1.get("ok"))
            self.assertTrue(r2.get("ok"))
            self.assertEqual(1, r1.get("processed"))
            # idempotent repeat should not process again
            self.assertEqual(0, r2.get("processed"))
            self.assertEqual(beads_after_1, beads_after_2)


if __name__ == "__main__":
    unittest.main()
