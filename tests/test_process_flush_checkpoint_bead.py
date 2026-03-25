import tempfile
import unittest

from core_memory.runtime.engine import process_turn_finalized, process_flush
from core_memory.persistence.store import MemoryStore


class TestProcessFlushCheckpointBead(unittest.TestCase):
    def test_process_flush_writes_checkpoint_bead(self):
        with tempfile.TemporaryDirectory() as td:
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="remember this",
                assistant_final="Decision with causal shape",
            )
            self.assertTrue(out.get("ok"))

            f = process_flush(
                root=td,
                session_id="s1",
                promote=True,
                token_budget=900,
                max_beads=10,
                flush_tx_id="flush-test-1",
            )
            self.assertTrue(f.get("ok"))
            self.assertTrue(f.get("checkpoint_bead_id"))
            self.assertTrue(f.get("checkpoint_bead_created"))

            s = MemoryStore(td)
            rows = s.query(type="process_flush", session_id="s1", limit=10)
            self.assertEqual(1, len(rows))
            self.assertEqual("flush-test-1", rows[0].get("flush_tx_id"))
            self.assertEqual("t1", rows[0].get("latest_done_turn_id"))

    def test_process_flush_checkpoint_idempotent_by_flush_tx(self):
        with tempfile.TemporaryDirectory() as td:
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="remember this",
                assistant_final="Decision with causal shape",
            )
            self.assertTrue(out.get("ok"))

            f1 = process_flush(
                root=td,
                session_id="s1",
                promote=True,
                token_budget=900,
                max_beads=10,
                flush_tx_id="flush-test-1",
            )
            self.assertTrue(f1.get("ok"))

            # Duplicate tx should skip flush cycle and not create extra checkpoint beads.
            f2 = process_flush(
                root=td,
                session_id="s1",
                promote=True,
                token_budget=900,
                max_beads=10,
                flush_tx_id="flush-test-1",
            )
            self.assertTrue(f2.get("ok"))

            s = MemoryStore(td)
            rows = s.query(type="process_flush", session_id="s1", limit=10)
            self.assertEqual(1, len(rows))


if __name__ == "__main__":
    unittest.main()
