import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.runtime.engine import process_turn_finalized, process_flush
from core_memory.runtime.worker import SidecarPolicy
from core_memory.retrieval.tools.memory import execute


class TestV2P19TurnToRetrievalContract(unittest.TestCase):
    def test_turn_creates_semantic_bead_via_canonical_path(self):
        with tempfile.TemporaryDirectory() as td:
            out = process_turn_finalized(
                root=td,
                session_id="s19",
                turn_id="t1",
                user_query="remember we decided to use crawler-reviewed creation",
                assistant_final="Decision: use crawler-reviewed semantic bead creation for canonical turn path.",
                policy=SidecarPolicy(),
            )
            self.assertTrue(out.get("ok"))

            sf = Path(td) / ".beads" / "session-s19.jsonl"
            rows = []
            if sf.exists():
                rows = [json.loads(line) for line in sf.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertGreaterEqual(len(rows), 1, "Expected canonical turn path to append at least one semantic bead")

    def test_flush_after_turn_populates_rolling_window(self):
        with tempfile.TemporaryDirectory() as td:
            process_turn_finalized(
                root=td,
                session_id="s19",
                turn_id="t1",
                user_query="remember continuity item",
                assistant_final="Outcome: continuity item captured for rolling context.",
                policy=SidecarPolicy(),
            )
            fl = process_flush(root=td, session_id="s19", promote=False, token_budget=800, max_beads=40, source="flush_hook")
            self.assertTrue(fl.get("ok"))

            rr = Path(td) / "rolling-window.records.json"
            self.assertTrue(rr.exists())
            payload = json.loads(rr.read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(payload.get("records") or []), 1, "Expected rolling window to include new turn bead")

    def test_retrieval_can_find_new_turn_memory(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"}, clear=False):
            process_turn_finalized(
                root=td,
                session_id="s19",
                turn_id="t1",
                user_query="remember that we retired sidecar and moved to event modules",
                assistant_final="Decision: event_* modules are canonical runtime surfaces.",
                policy=SidecarPolicy(),
            )
            result = execute(
                {
                    "raw_query": "what did we decide about sidecar modules",
                    "intent": "causal",
                    "k": 5,
                },
                root=td,
                explain=True,
            )
            self.assertTrue(result.get("ok"))
            self.assertTrue(result.get("results"), "Expected retrieval results for newly created turn memory")


if __name__ == "__main__":
    unittest.main()
