import tempfile
import unittest

from core_memory.store import MemoryStore
from core_memory.tools.memory_reason import memory_reason


class TestRouterHintRetryR4(unittest.TestCase):
    def test_hint_used_only_after_low_quality_primary(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="context", title="General memory", summary=["misc note"], session_id="main", source_turn_ids=["t1"])
            out = memory_reason("remember misc", root=td)
            self.assertTrue(out.get("ok"))
            intent = out.get("intent") or {}
            self.assertIn("used_hint_retry", intent)
            self.assertIn("quality_score", (out.get("confidence") or {}))


if __name__ == "__main__":
    unittest.main()
