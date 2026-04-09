from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.runtime.engine import process_flush, process_turn_finalized


class TestFlushAsyncSideEffectsSlice59A(unittest.TestCase):
    def test_flush_enqueues_post_write_side_effects(self):
        with tempfile.TemporaryDirectory(prefix="cm-flush-se-") as td, patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_ASYNC_SIDE_EFFECTS_MODE": "enqueue",
                "CORE_MEMORY_ASYNC_SIDE_EFFECTS": "semantic-rebuild,dreamer-run,neo4j-sync,health-recompute",
            },
            clear=False,
        ):
            t = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="remember this",
                assistant_final="decision captured",
            )
            self.assertTrue(t.get("ok"))

            out = process_flush(root=td, session_id="s1", promote=True, token_budget=1200, max_beads=12)
            self.assertTrue(out.get("ok"))
            side = out.get("post_write_side_effects") or {}
            self.assertEqual("enqueue", side.get("mode"))
            enq = side.get("enqueued") or {}
            self.assertIn("semantic-rebuild", enq)
            self.assertIn("dreamer-run", enq)
            self.assertIn("neo4j-sync", enq)
            self.assertIn("health-recompute", enq)

            se_q = Path(td) / ".beads" / "events" / "side-effects-queue.json"
            self.assertTrue(se_q.exists())
            rows = json.loads(se_q.read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(rows), 3)
            kinds = {str((r or {}).get("kind") or "") for r in rows if isinstance(r, dict)}
            self.assertIn("dreamer-run", kinds)
            self.assertIn("neo4j-sync", kinds)
            self.assertIn("health-recompute", kinds)

    def test_flush_respects_dreamer_trigger_filter(self):
        with tempfile.TemporaryDirectory(prefix="cm-flush-se-") as td, patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_ASYNC_SIDE_EFFECTS_MODE": "enqueue",
                "CORE_MEMORY_ASYNC_SIDE_EFFECTS": "dreamer-run",
                "CORE_MEMORY_DREAMER_TRIGGERS": "session_end",
            },
            clear=False,
        ):
            t = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="remember this",
                assistant_final="decision captured",
            )
            self.assertTrue(t.get("ok"))

            out = process_flush(root=td, session_id="s1", promote=True, token_budget=1200, max_beads=12)
            self.assertTrue(out.get("ok"))
            side = out.get("post_write_side_effects") or {}
            enq = side.get("enqueued") or {}
            dr = enq.get("dreamer-run") or {}
            self.assertTrue(dr.get("skipped"))
            self.assertEqual("trigger_not_enabled", dr.get("reason"))

    def test_flush_enqueues_dreamer_with_mode_from_env(self):
        with tempfile.TemporaryDirectory(prefix="cm-flush-se-") as td, patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_ASYNC_SIDE_EFFECTS_MODE": "enqueue",
                "CORE_MEMORY_ASYNC_SIDE_EFFECTS": "dreamer-run",
                "CORE_MEMORY_DREAMER_MODE": "reviewed_apply",
            },
            clear=False,
        ):
            t = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="remember this",
                assistant_final="decision captured",
            )
            self.assertTrue(t.get("ok"))

            out = process_flush(root=td, session_id="s1", promote=True, token_budget=1200, max_beads=12)
            self.assertTrue(out.get("ok"))

            q = Path(td) / ".beads" / "events" / "side-effects-queue.json"
            rows = json.loads(q.read_text(encoding="utf-8"))
            dream_rows = [r for r in rows if str((r or {}).get("kind") or "") == "dreamer-run"]
            self.assertTrue(dream_rows)
            payload = dream_rows[0].get("payload") or {}
            self.assertEqual("reviewed_apply", payload.get("mode"))

    def test_flush_side_effects_can_be_disabled(self):
        with tempfile.TemporaryDirectory(prefix="cm-flush-se-") as td, patch.dict(
            "os.environ",
            {"CORE_MEMORY_ASYNC_SIDE_EFFECTS_MODE": "off"},
            clear=False,
        ):
            t = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="remember this",
                assistant_final="decision captured",
            )
            self.assertTrue(t.get("ok"))

            out = process_flush(root=td, session_id="s1", promote=True, token_budget=1200, max_beads=12)
            self.assertTrue(out.get("ok"))
            side = out.get("post_write_side_effects") or {}
            self.assertEqual("off", side.get("mode"))
            self.assertTrue(bool(side.get("skipped")))


if __name__ == "__main__":
    unittest.main()
