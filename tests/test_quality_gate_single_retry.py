import tempfile
import unittest
from pathlib import Path

from core_memory.retrieval.quality_gate import quality_gate_decision
from core_memory.tools.memory_reason import memory_reason
from core_memory.store import MemoryStore


class TestQualityGateSingleRetry(unittest.TestCase):
    def test_quality_gate_retries_once(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="context", title="[[reply_to_current]]", summary=["misc"], session_id="main", source_turn_ids=["t1"])
            out = memory_reason("help", root=td, debug=True)
            self.assertTrue(out.get("ok"))
            dbg = out.get("retrieval_debug") or {}
            inner = dbg.get("debug") or {}
            # debug shape includes gate and optional retry
            self.assertIn("gate", inner)
            self.assertIn("retry", inner)

    def test_gate_reason_low_score(self):
        gate = quality_gate_decision([
            {"rerank_score": 0.1, "features": {"has_decision": 0, "has_evidence": 0, "has_outcome": 0}}
        ])
        self.assertTrue(gate.get("retry"))


if __name__ == "__main__":
    unittest.main()
