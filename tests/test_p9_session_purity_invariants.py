import json
import os
import tempfile
import unittest
from pathlib import Path

from core_memory.runtime.engine import read_live_session
from core_memory.persistence.store import MemoryStore
from core_memory.integrations.openclaw_runtime import resolve_core_session_id


class TestP9SessionPurityInvariants(unittest.TestCase):
    def test_bridge_default_does_not_collapse_session(self):
        resolved = resolve_core_session_id(
            openclaw_session_id="sess-123",
            core_session_id=None,
            collapse_to_main=False,
        )
        self.assertEqual("sess-123", resolved)

    def test_bridge_collapse_mode_is_explicit(self):
        resolved = resolve_core_session_id(
            openclaw_session_id="sess-123",
            core_session_id=None,
            collapse_to_main=True,
        )
        self.assertEqual("main", resolved)

    def test_live_session_strict_default_and_opt_in_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="context", title="x", summary=["y"], session_id="s1", source_turn_ids=["t1"])

            sf = Path(td) / ".beads" / "session-s1.jsonl"
            if sf.exists():
                sf.unlink()

            old = os.environ.get("CORE_MEMORY_LIVE_SESSION_ALLOW_INDEX_FALLBACK")
            try:
                os.environ["CORE_MEMORY_LIVE_SESSION_ALLOW_INDEX_FALLBACK"] = "0"
                strict = read_live_session(root=td, session_id="s1")
                self.assertEqual("session_surface_empty", strict.get("authority"))
                self.assertEqual(0, strict.get("count"))

                os.environ["CORE_MEMORY_LIVE_SESSION_ALLOW_INDEX_FALLBACK"] = "1"
                compat = read_live_session(root=td, session_id="s1")
                self.assertEqual("index_fallback", compat.get("authority"))
                self.assertEqual(1, compat.get("count"))
            finally:
                if old is None:
                    os.environ.pop("CORE_MEMORY_LIVE_SESSION_ALLOW_INDEX_FALLBACK", None)
                else:
                    os.environ["CORE_MEMORY_LIVE_SESSION_ALLOW_INDEX_FALLBACK"] = old

    def test_kickoff_doc_marks_step_progress(self):
        p = Path(__file__).resolve().parents[1] / "docs" / "v2_p9_kickoff.md"
        payload = p.read_text(encoding="utf-8")
        self.assertIn("Step plan", payload)
        self.assertIn("Step 3 completion notes", payload)


if __name__ == "__main__":
    unittest.main()
