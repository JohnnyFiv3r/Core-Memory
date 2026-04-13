import tempfile
import unittest
from pathlib import Path

from core_memory.integrations.api import (
    inspect_state,
    inspect_bead,
    inspect_bead_hydration,
    inspect_claim_slot,
    list_turn_summaries,
)
from core_memory.runtime.engine import process_turn_finalized


class TestInspectApi(unittest.TestCase):
    def test_inspect_family_smoke(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")

            process_turn_finalized(
                root=root,
                session_id="s1",
                turn_id="t1",
                transaction_id="tx1",
                trace_id="tr1",
                user_query="remember that postgres won benchmarks",
                assistant_final="noted: postgres won benchmarks",
                origin="TEST",
                metadata={"source": "test"},
            )

            state = inspect_state(root=root, session_id="s1")
            self.assertTrue(state.get("ok"))
            self.assertIn("memory", state)
            self.assertIn("claims", state)
            self.assertIn("entities", state)
            self.assertIn("runtime", state)

            beads = list((state.get("memory") or {}).get("beads") or [])
            self.assertGreaterEqual(len(beads), 1)
            bead_id = str(beads[0].get("id") or "")
            self.assertTrue(bead_id)

            bead = inspect_bead(root=root, bead_id=bead_id)
            self.assertTrue(isinstance(bead, dict))

            hyd = inspect_bead_hydration(root=root, bead_id=bead_id)
            self.assertTrue(hyd.get("ok"))
            self.assertIn("hydrated", hyd)

            slot = inspect_claim_slot(root=root, subject="user", slot="preferred_db")
            self.assertTrue(slot.get("ok"))
            self.assertIn("slot_key", slot)
            self.assertIn("row", slot)

            turns = list_turn_summaries(root=root, session_id="s1", limit=10)
            self.assertTrue(turns.get("ok"))
            self.assertTrue(isinstance(turns.get("items"), list))


if __name__ == "__main__":
    unittest.main()
