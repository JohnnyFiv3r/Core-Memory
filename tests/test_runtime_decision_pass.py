import tempfile
import unittest

from core_memory.runtime.decision_pass import run_session_decision_pass
from core_memory.persistence.store import MemoryStore


class TestRuntimeDecisionPass(unittest.TestCase):
    def test_runtime_entrypoint_runs_store_backed_decision_pass(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b = s.add_bead(type="context", title="seed", summary=["x"], session_id="s1", source_turn_ids=["t0"])
            out = run_session_decision_pass(root=td, session_id="s1", visible_bead_ids=[b], turn_id="t1")
            self.assertTrue(out.get("ok"))
            self.assertGreaterEqual(int((out.get("counts") or {}).get("evaluated", 0)), 1)


if __name__ == "__main__":
    unittest.main()
