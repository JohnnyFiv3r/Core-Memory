import tempfile
import unittest

from core_memory.memory_engine import process_turn_finalized, process_flush
from core_memory.sidecar_worker import SidecarPolicy
from core_memory.store import MemoryStore


class TestMemoryEngine(unittest.TestCase):
    def test_process_turn_finalized(self):
        with tempfile.TemporaryDirectory() as td:
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="remember this",
                assistant_final="Decision: use memory engine entrypoint",
                policy=SidecarPolicy(create_threshold=0.6),
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual("canonical_in_process", out.get("authority_path"))
            self.assertTrue((out.get("engine") or {}).get("normalized"))
            self.assertTrue((out.get("crawler_handoff") or {}).get("required"))

    def test_process_flush(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="context", title="x", summary=["y"], session_id="main", source_turn_ids=["t1"])
            out = process_flush(
                root=td,
                session_id="main",
                promote=False,
                token_budget=300,
                max_beads=20,
                source="admin_cli",
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual("canonical_in_process", out.get("authority_path"))
            self.assertEqual("process_flush", (out.get("engine") or {}).get("entry"))


if __name__ == "__main__":
    unittest.main()
