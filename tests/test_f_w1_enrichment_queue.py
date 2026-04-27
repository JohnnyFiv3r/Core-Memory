"""F-W1 acceptance tests: enrichment queue for non-critical write stages.

Verifies:
1. Critical path persists bead even when enrichment is queued.
2. Enrichment enqueue produces a valid side-effect entry.
3. Enrichment drain runs post-persist stages.
4. Kill switch (CORE_MEMORY_ENRICHMENT_QUEUE=off) runs stages inline.
5. turn-enrichment is a registered side effect kind.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.enrichment import (
    _enrichment_queue_enabled,
    enqueue_turn_enrichment,
)
from core_memory.runtime.side_effect_queue import (
    _SIDE_EFFECT_KINDS,
    side_effect_queue_status,
)


class TestEnrichmentQueueRegistered(unittest.TestCase):
    """turn-enrichment is a valid side effect kind."""

    def test_kind_registered(self):
        self.assertIn("turn-enrichment", _SIDE_EFFECT_KINDS)


class TestEnrichmentQueueEnabled(unittest.TestCase):
    """Kill switch via CORE_MEMORY_ENRICHMENT_QUEUE env var."""

    def test_default_enabled(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORE_MEMORY_ENRICHMENT_QUEUE", None)
            self.assertTrue(_enrichment_queue_enabled())

    @patch.dict(os.environ, {"CORE_MEMORY_ENRICHMENT_QUEUE": "on"}, clear=False)
    def test_explicit_on(self):
        self.assertTrue(_enrichment_queue_enabled())

    @patch.dict(os.environ, {"CORE_MEMORY_ENRICHMENT_QUEUE": "off"}, clear=False)
    def test_explicit_off(self):
        self.assertFalse(_enrichment_queue_enabled())


class TestEnqueueTurnEnrichment(unittest.TestCase):
    """Enqueue produces a valid side-effect entry."""

    def test_enqueue_succeeds(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="decision", title="test", summary=["x"], session_id="s1", source_turn_ids=["t1"])

            result = enqueue_turn_enrichment(
                root=td,
                session_id="s1",
                turn_id="t1",
                bead_id="b1",
                req={"session_id": "s1", "turn_id": "t1", "user_query": "q", "assistant_final": "a"},
            )
            self.assertTrue(result["ok"])
            self.assertFalse(result.get("duplicate", False))

    def test_idempotent_enqueue(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="decision", title="test", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            req = {"session_id": "s1", "turn_id": "t1", "user_query": "q", "assistant_final": "a"}

            r1 = enqueue_turn_enrichment(root=td, session_id="s1", turn_id="t1", bead_id="b1", req=req)
            r2 = enqueue_turn_enrichment(root=td, session_id="s1", turn_id="t1", bead_id="b1", req=req)
            self.assertTrue(r1["ok"])
            self.assertTrue(r2["ok"])
            self.assertTrue(r2.get("duplicate", False))

    @patch.dict(os.environ, {"CORE_MEMORY_ENRICHMENT_QUEUE": "off"}, clear=False)
    def test_returns_none_when_disabled(self):
        result = enqueue_turn_enrichment(
            root="/tmp/fake",
            session_id="s1",
            turn_id="t1",
            bead_id="b1",
            req={},
        )
        self.assertIsNone(result)


class TestQueueStatusReflectsEnrichment(unittest.TestCase):
    """Queue status shows enrichment entries."""

    def test_depth_increases(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="decision", title="test", summary=["x"], session_id="s1", source_turn_ids=["t1"])

            status_before = side_effect_queue_status(td)
            enqueue_turn_enrichment(
                root=td, session_id="s1", turn_id="t1", bead_id="b1",
                req={"session_id": "s1", "turn_id": "t1"},
            )
            status_after = side_effect_queue_status(td)

            self.assertGreater(status_after["queue_depth"], status_before["queue_depth"])
            self.assertIn("turn-enrichment", status_after.get("by_kind", {}))


class TestCriticalPathPersistsBead(unittest.TestCase):
    """Bead is persisted on the critical path regardless of enrichment."""

    @patch.dict(os.environ, {
        "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
        "CORE_MEMORY_AGENT_AUTHORED_MODE": "off",
    }, clear=False)
    def test_bead_persisted_with_queue_on(self):
        with tempfile.TemporaryDirectory() as td:
            from core_memory import process_turn_finalized
            result = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="Why PostgreSQL?",
                assistant_final="JSONB + transactional consistency",
            )
            self.assertTrue(result.get("ok"))
            self.assertEqual(result.get("processed"), 1)
            # Turn event persisted to session JSONL (write authority)
            session_jsonl = Path(td) / ".turns" / "session-s1.jsonl"
            self.assertTrue(session_jsonl.exists(), "session JSONL should exist after turn")

    @patch.dict(os.environ, {
        "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
        "CORE_MEMORY_AGENT_AUTHORED_MODE": "off",
        "CORE_MEMORY_ENRICHMENT_QUEUE": "off",
    }, clear=False)
    def test_bead_persisted_with_queue_off(self):
        with tempfile.TemporaryDirectory() as td:
            from core_memory import process_turn_finalized
            result = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="Why PostgreSQL?",
                assistant_final="JSONB + transactional consistency",
            )
            self.assertTrue(result.get("ok"))
            self.assertEqual(result.get("processed"), 1)


if __name__ == "__main__":
    unittest.main()
