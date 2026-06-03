"""Per-request bead-judge directive.

CORE_MEMORY_BEAD_JUDGE_FALLBACK / CORE_MEMORY_BEAD_FIELD_JUDGE_MODE are
process-global env vars. When the demo runs concurrent benchmark jobs in one
process the env is shared: a judge-mode job could leak into a deterministic
job on another thread. The per-request directive (req["_bead_judge"] or
metadata["bead_judge"]) overrides env at call time without mutating global
state, so jobs stay isolated.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from core_memory.runtime.engine import (
    _judge_fallback_enabled,
    _maybe_apply_judge_fallback,
    _req_judge_directive,
)
from core_memory.policy.bead_judge import judge_bead_fields


class TestReqJudgeDirective(unittest.TestCase):
    def test_req_field_takes_priority_over_env(self):
        # Per-request "off" should disable fallback even when env says "1"
        req = {"_bead_judge": "off"}
        with patch.dict("os.environ", {"CORE_MEMORY_BEAD_JUDGE_FALLBACK": "1"}, clear=False):
            self.assertFalse(_judge_fallback_enabled(req))

    def test_metadata_field_activates_fallback_without_env(self):
        req = {"metadata": {"bead_judge": "llm"}}
        with patch.dict("os.environ", {"CORE_MEMORY_BEAD_JUDGE_FALLBACK": "0"}, clear=False):
            self.assertTrue(_judge_fallback_enabled(req))

    def test_none_req_falls_back_to_env(self):
        with patch.dict("os.environ", {"CORE_MEMORY_BEAD_JUDGE_FALLBACK": "1"}, clear=False):
            self.assertTrue(_judge_fallback_enabled(None))
        with patch.dict("os.environ", {"CORE_MEMORY_BEAD_JUDGE_FALLBACK": "0"}, clear=False):
            self.assertFalse(_judge_fallback_enabled(None))

    def test_req_directive_parsed_correctly(self):
        self.assertIsNone(_req_judge_directive(None))
        self.assertIsNone(_req_judge_directive({}))
        self.assertEqual("llm", _req_judge_directive({"_bead_judge": "LLM"}))
        self.assertEqual("heuristic", _req_judge_directive({"metadata": {"bead_judge": "heuristic"}}))

    def test_maybe_apply_uses_req_mode(self):
        # req directive "heuristic" should override env "off" and call judge in heuristic mode
        row = {"type": "context", "title": "T", "summary": ["S"]}
        req = {"_bead_judge": "heuristic"}
        with patch.dict("os.environ", {"CORE_MEMORY_BEAD_JUDGE_FALLBACK": "0"}, clear=False), \
             patch("core_memory.runtime.engine.judge_bead_fields", return_value={
                 "type": "context", "title": "T", "summary": ["S"], "entities": ["Alice"],
                 "judge": {"mode": "heuristic"},
             }) as mock_judge:
            out = _maybe_apply_judge_fallback(row, "q", "a", req=req)
        # judge was called despite env being "0"
        mock_judge.assert_called_once()
        # mode kwarg was forwarded
        _, kwargs = mock_judge.call_args
        self.assertEqual("heuristic", kwargs.get("mode"))
        self.assertIn("Alice", out.get("entities") or [])

    def test_judge_bead_fields_mode_kwarg_overrides_env(self):
        with patch("core_memory.policy.bead_judge._llm_judge_provider_neutral", return_value=None), \
             patch("core_memory.policy.bead_judge._llm_judge_anthropic", return_value=None), \
             patch("core_memory.policy.bead_judge._llm_judge_openai", return_value=None), \
             patch.dict("os.environ", {"CORE_MEMORY_BEAD_FIELD_JUDGE_MODE": "llm"}, clear=False):
            out = judge_bead_fields("q", "a", mode="heuristic")
        self.assertEqual("heuristic", (out.get("judge") or {}).get("mode"))


if __name__ == "__main__":
    unittest.main()
