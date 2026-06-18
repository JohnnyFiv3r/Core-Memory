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
from core_memory.runtime.passes.enrichment import (
    _enrichment_queue_enabled,
    enqueue_turn_enrichment,
    run_turn_enrichment,
)
from core_memory.runtime.queue.side_effect_queue import (
    _SIDE_EFFECT_KINDS,
    _queue_path,
    side_effect_queue_status,
)


def _queued_payload_for_kind(root: str, kind: str) -> dict:
    import json
    queue = json.loads(_queue_path(root).read_text(encoding="utf-8"))
    for item in queue:
        if str((item or {}).get("kind") or "") == kind:
            return dict((item or {}).get("payload") or {})
    raise AssertionError(f"queued side effect kind not found: {kind}")


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

            payload = _queued_payload_for_kind(td, "turn-enrichment")
            self.assertEqual("session_enrichment_delta.v1", payload["enrichment_delta"]["schema"])
            self.assertEqual("enrich-s1-t1", payload["enrichment_delta"]["source"]["idempotency_key"])

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

    def test_enqueue_persists_projected_reviewed_updates(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            visible_bead = s.add_bead(type="decision", title="test", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            reviewed_updates = {
                "promotions": [visible_bead],
                "associations": [{"source_bead_id": visible_bead, "target_bead_id": "outside-window", "relationship": "supports"}],
            }

            result = enqueue_turn_enrichment(
                root=td,
                session_id="s1",
                turn_id="t1",
                bead_id=visible_bead,
                req={"session_id": "s1", "turn_id": "t1", "user_query": "q", "assistant_final": "a"},
                reviewed_updates=reviewed_updates,
                crawler_ctx={"session_id": "s1", "visible_bead_ids": [visible_bead]},
            )
            self.assertTrue(result["ok"])

            payload = _queued_payload_for_kind(td, "turn-enrichment")
            self.assertEqual([visible_bead], payload["reviewed_updates"]["promotions"])
            self.assertEqual([], payload["reviewed_updates"]["associations"])
            self.assertEqual(1, payload["enrichment_delta"]["diagnostics"]["quarantined_counts"]["associations"])
            self.assertEqual(1, payload["delta_quarantine"]["written"])

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


class TestRunTurnEnrichmentDeltaAuthority(unittest.TestCase):
    """Queued enrichment consumes normalized delta projection when available."""

    @patch("core_memory.integrations.openclaw.flags.claim_layer_enabled", return_value=False)
    @patch("core_memory.runtime.engine._emit_agent_turn_quality_metric", return_value=None)
    @patch("core_memory.runtime.engine.run_session_decision_pass", return_value={})
    @patch("core_memory.runtime.engine._queue_preview_associations", return_value=0)
    @patch("core_memory.association.crawler_contract.merge_crawler_updates", return_value={"associations_appended": 0})
    @patch("core_memory.runtime.passes.association_pass.run_association_pass", return_value={"created_bead_ids": []})
    def test_enrichment_delta_overrides_parallel_reviewed_updates(
        self,
        association_spy,
        _merge_spy,
        _preview_spy,
        _decision_spy,
        _quality_spy,
        _claim_enabled_spy,
    ):
        from core_memory.runtime.session.session_enrichment_delta import crawler_updates_to_delta

        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            visible_bead = store.add_bead(type="decision", title="visible", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            delta = crawler_updates_to_delta(
                session_id="s1",
                turn_id="t1",
                updates={"promotions": [visible_bead]},
                crawler_ctx={"session_id": "s1", "visible_bead_ids": [visible_bead]},
                source_kind="queued",
                idempotency_key="enrich-s1-t1",
            )

            out = run_turn_enrichment(
                root=td,
                payload={
                    "session_id": "s1",
                    "turn_id": "t1",
                    "bead_id": visible_bead,
                    "reviewed_updates": {"promotions": ["raw-reviewed-update-should-not-win"]},
                    "enrichment_delta": delta,
                    "crawler_visible_bead_ids": [visible_bead],
                },
            )

        self.assertTrue(out["ok"])
        self.assertEqual("session_enrichment_delta.v1", out["enrichment_delta"]["schema"])
        self.assertEqual("enrich-s1-t1", out["enrichment_delta"]["idempotency_key"])
        self.assertEqual(1, out["enrichment_delta"]["accepted_counts"]["promotions"])
        updates = association_spy.call_args.kwargs["updates"]
        self.assertEqual([visible_bead], updates["promotions"])
        self.assertNotIn("raw-reviewed-update-should-not-win", updates["promotions"])


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
                turns=[
                    {"speaker": "user", "role": "user", "content": "Why PostgreSQL?"},
                    {
                        "speaker": "assistant",
                        "role": "assistant",
                        "content": "JSONB + transactional consistency",
                    },
                ],
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
                turns=[
                    {"speaker": "user", "role": "user", "content": "Why PostgreSQL?"},
                    {
                        "speaker": "assistant",
                        "role": "assistant",
                        "content": "JSONB + transactional consistency",
                    },
                ],
            )
            self.assertTrue(result.get("ok"))
            self.assertEqual(result.get("processed"), 1)


class TestClaimDecisionVisibleWindowExpansion(unittest.TestCase):
    """Claim decision pass includes recalled window beads, not just current-session beads."""

    @patch.dict(os.environ, {
        "CORE_MEMORY_ENRICHMENT_QUEUE": "off",
        "CORE_MEMORY_AGENT_AUTHORED_MODE": "off",
    }, clear=False)
    @patch("core_memory.runtime.engine.claim_layer_enabled", return_value=True)
    @patch("core_memory.runtime.engine.emit_claim_updates", return_value=[])
    @patch("core_memory.runtime.engine.extract_and_attach_claims")
    def test_inline_claim_updates_include_window_bead_ids(
        self,
        extract_spy,
        emit_spy,
        _claim_enabled_spy,
    ):
        from core_memory import process_turn_finalized

        extract_spy.return_value = {
            "canonical_bead_id": "turn-bead",
            "claims_batch": [
                {"id": "claim-new", "subject": "user", "slot": "preference", "value": "tea"}
            ],
        }

        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            session_bead = store.add_bead(
                type="context",
                title="session bead",
                summary=["current session"],
                session_id="s1",
                source_turn_ids=["t0"],
            )
            recalled_bead = store.add_bead(
                type="context",
                title="recalled bead",
                summary=["prior session"],
                session_id="s0",
                source_turn_ids=["old-turn"],
            )

            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                turns=[
                    {"speaker": "user", "role": "user", "content": "I prefer tea now"},
                    {"speaker": "assistant", "role": "assistant", "content": "Got it"},
                ],
                window_bead_ids=[recalled_bead],
            )

        self.assertTrue(out.get("ok"))
        visible = set(emit_spy.call_args.kwargs["visible_bead_ids"])
        self.assertIn(str(session_bead), visible)
        self.assertIn(str(recalled_bead), visible)

    @patch("core_memory.config.feature_flags.claim_layer_enabled", return_value=True)
    @patch("core_memory.runtime.engine.emit_claim_updates", return_value=[])
    @patch("core_memory.runtime.engine.extract_and_attach_claims")
    @patch("core_memory.runtime.engine._emit_agent_turn_quality_metric", return_value=None)
    def test_queued_claim_updates_include_window_bead_ids(
        self,
        _quality_spy,
        extract_spy,
        emit_spy,
        _claim_enabled_spy,
    ):
        extract_spy.return_value = {
            "canonical_bead_id": "turn-bead",
            "claims_batch": [
                {"id": "claim-new", "subject": "user", "slot": "preference", "value": "tea"}
            ],
        }

        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            session_bead = store.add_bead(
                type="context",
                title="session bead",
                summary=["current session"],
                session_id="s1",
                source_turn_ids=["t0"],
            )
            recalled_bead = store.add_bead(
                type="context",
                title="recalled bead",
                summary=["prior session"],
                session_id="s0",
                source_turn_ids=["old-turn"],
            )

            out = run_turn_enrichment(
                root=td,
                payload={
                    "session_id": "s1",
                    "turn_id": "t1",
                    "bead_id": "turn-bead",
                    "user_query": "I prefer tea now",
                    "reviewed_updates": {},
                    "crawler_visible_bead_ids": [session_bead],
                    "window_bead_ids": [recalled_bead],
                },
            )

        self.assertTrue(out.get("ok"))
        visible = set(emit_spy.call_args.kwargs["visible_bead_ids"])
        self.assertIn(str(session_bead), visible)
        self.assertIn(str(recalled_bead), visible)

if __name__ == "__main__":
    unittest.main()
