from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core_memory.runtime.engine import _enforce_structural_invariants, _maybe_apply_judge_fallback


class TestEngineStructuralInvariantsPhase3B(unittest.TestCase):
    def test_structural_invariants_preserve_semantic_fields(self):
        req = {
            "session_id": "s1",
            "turn_id": "t1",
            "speakers": ["user", "assistant"],
        }
        row = {
            "type": "decision",
            "title": "Agent-authored title",
            "summary": ["Agent-authored summary"],
            "retrieval_eligible": True,
            "retrieval_title": "Search title",
            "retrieval_facts": ["Durable fact"],
            "entities": ["Redis"],
            "topics": ["cache"],
        }
        with tempfile.TemporaryDirectory() as td:
            out = _enforce_structural_invariants(td, req, row)

        self.assertEqual("decision", out.get("type"))
        self.assertEqual("Agent-authored title", out.get("title"))
        self.assertEqual(["Agent-authored summary"], out.get("summary"))
        self.assertTrue(out.get("retrieval_eligible"))
        self.assertEqual("Search title", out.get("retrieval_title"))
        self.assertEqual(["Durable fact"], out.get("retrieval_facts"))
        self.assertEqual(["Redis"], out.get("entities"))
        self.assertEqual(["cache"], out.get("topics"))
        self.assertEqual("s1", out.get("session_id"))
        self.assertIn("t1", out.get("source_turn_ids") or [])
        self.assertTrue(str(out.get("bead_id") or "").startswith("bead-"))

    def test_structural_invariants_do_not_call_judge(self):
        req = {"session_id": "s1", "turn_id": "t1"}
        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.runtime.engine.judge_bead_fields",
            side_effect=AssertionError("judge should not run from structural invariants"),
        ):
            out = _enforce_structural_invariants(td, req, {"type": "invalid", "title": "T", "summary": ["S"]})
        self.assertEqual("context", out.get("type"))

    def test_judge_fallback_fills_missing_only_when_enabled(self):
        row = {
            "type": "context",
            "title": "Agent title",
            "summary": ["Agent summary"],
            "retrieval_eligible": True,
        }
        judged = {
            "type": "decision",
            "title": "Judge title",
            "summary": ["Judge summary"],
            "retrieval_title": "Judge retrieval",
            "retrieval_facts": ["Judge fact"],
            "entities": ["Redis"],
            "topics": ["cache"],
            "judge": {"mode": "llm"},
        }
        with patch.dict("os.environ", {"CORE_MEMORY_BEAD_JUDGE_FALLBACK": "1"}, clear=False), patch(
            "core_memory.runtime.engine.judge_bead_fields", return_value=judged
        ):
            out = _maybe_apply_judge_fallback(row, "q", "a")

        self.assertEqual("context", out.get("type"))
        self.assertEqual("Agent title", out.get("title"))
        self.assertEqual(["Agent summary"], out.get("summary"))
        self.assertEqual(["Redis"], out.get("entities"))
        self.assertIn("llm_judged", out.get("tags") or [])


if __name__ == "__main__":
    unittest.main()
