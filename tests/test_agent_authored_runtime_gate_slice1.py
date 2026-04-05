from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.runtime.engine import process_turn_finalized


class TestAgentAuthoredRuntimeGateSlice1(unittest.TestCase):
    def test_strict_mode_blocks_when_updates_missing(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "0",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="q",
                assistant_final="a",
                metadata={},
            )
            self.assertFalse(out.get("ok"))
            self.assertEqual("agent_updates_missing", out.get("error_code"))
            gate = (out.get("crawler_handoff") or {}).get("agent_authored_gate") or {}
            self.assertTrue(gate.get("required"))
            self.assertTrue(gate.get("blocked"))

            idx = Path(td) / ".beads" / "index.json"
            self.assertFalse(idx.exists())

    def test_strict_mode_blocks_when_updates_invalid(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "0",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="q",
                assistant_final="a",
                metadata={"crawler_updates": {"beads_create": [{"title": "only title"}]}} ,
            )
            self.assertFalse(out.get("ok"))
            self.assertEqual("agent_bead_fields_missing", out.get("error_code"))

    def test_strict_mode_fail_open_uses_default_fallback(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "1",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="remember this",
                assistant_final="decision text",
                metadata={},
            )
            self.assertTrue(out.get("ok"))
            gate = (out.get("crawler_handoff") or {}).get("agent_authored_gate") or {}
            self.assertTrue(gate.get("used_fallback"))
            self.assertEqual("default_fallback", gate.get("source"))

    def test_strict_mode_accepts_valid_agent_updates(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "1",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "0",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="q",
                assistant_final="a",
                metadata={
                    "crawler_updates": {
                        "beads_create": [
                            {
                                "type": "decision",
                                "title": "Agent decided",
                                "summary": ["summary"],
                                "source_turn_ids": ["t1"],
                            }
                        ]
                    }
                },
            )
            self.assertTrue(out.get("ok"))
            gate = (out.get("crawler_handoff") or {}).get("agent_authored_gate") or {}
            self.assertEqual("metadata.crawler_updates", gate.get("source"))
            self.assertFalse(gate.get("used_fallback"))


if __name__ == "__main__":
    unittest.main()
