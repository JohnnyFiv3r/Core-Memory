import tempfile
import unittest

from core_memory.runtime.turn_quality import association_mix_stats
from core_memory.runtime.flush_state import read_flush_state, write_flush_state, upsert_process_flush_checkpoint_bead


class TestRuntimeSplitModulesSlice46A(unittest.TestCase):
    def test_association_mix_stats_counts_relationship_types(self):
        out = association_mix_stats(
            {
                "associations": [
                    {"relationship": "shared_tag"},
                    {"relationship": "follows"},
                    {"relationship": "precedes"},
                    {"relationship": "supports"},
                    {"relationship": "causes"},
                    {},
                    "bad-row",
                ]
            }
        )
        self.assertEqual(6, out.get("associations_total"))
        self.assertEqual(1, out.get("shared_tag_count"))
        self.assertEqual(2, out.get("temporal_count"))
        self.assertEqual(2, out.get("non_temporal_semantic_count"))

    def test_flush_state_roundtrip_and_checkpoint_idempotency(self):
        with tempfile.TemporaryDirectory() as td:
            state = {"sessions": {"s1": {"last_flushed_turn_id": "t1"}}}
            write_flush_state(td, state)
            self.assertEqual(state, read_flush_state(td))

            bead_id_1, created_1 = upsert_process_flush_checkpoint_bead(
                root=td,
                session_id="s1",
                flush_tx_id="fx-1",
                latest_turn_id="t1",
                latest_done_turn_id="t1",
                latest_turn_status="done",
                source="test",
                token_budget=100,
                max_beads=30,
                promote=False,
            )
            bead_id_2, created_2 = upsert_process_flush_checkpoint_bead(
                root=td,
                session_id="s1",
                flush_tx_id="fx-1",
                latest_turn_id="t1",
                latest_done_turn_id="t1",
                latest_turn_status="done",
                source="test",
                token_budget=100,
                max_beads=30,
                promote=False,
            )

            self.assertTrue(created_1)
            self.assertFalse(created_2)
            self.assertEqual(bead_id_1, bead_id_2)


if __name__ == "__main__":
    unittest.main()
