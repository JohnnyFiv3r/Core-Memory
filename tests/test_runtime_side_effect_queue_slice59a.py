from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.runtime.side_effect_queue import (
    drain_side_effect_queue,
    enqueue_side_effect_event,
    process_side_effect_event,
    side_effect_queue_status,
)


class TestRuntimeSideEffectQueueSlice59A(unittest.TestCase):
    def test_enqueue_rejects_unknown_kind(self):
        with tempfile.TemporaryDirectory(prefix="cm-se-") as td:
            out = enqueue_side_effect_event(root=td, kind="unknown-kind", payload={})
            self.assertFalse(out.get("ok"))
            err = out.get("error") or {}
            self.assertEqual("unknown_kind", err.get("code"))

    def test_enqueue_idempotency_key_dedupes(self):
        with tempfile.TemporaryDirectory(prefix="cm-se-") as td:
            one = enqueue_side_effect_event(
                root=td,
                kind="dreamer-run",
                payload={"session_id": "s1"},
                idempotency_key="dreamer:s1:tx1",
            )
            two = enqueue_side_effect_event(
                root=td,
                kind="dreamer-run",
                payload={"session_id": "s1"},
                idempotency_key="dreamer:s1:tx1",
            )
            self.assertTrue(one.get("ok"))
            self.assertTrue(two.get("ok"))
            self.assertFalse(bool(one.get("duplicate")))
            self.assertTrue(bool(two.get("duplicate")))

    def test_status_and_drain_with_stub_processor(self):
        with tempfile.TemporaryDirectory(prefix="cm-se-") as td:
            enqueue_side_effect_event(root=td, kind="dreamer-run", payload={"session_id": "s1"})
            enqueue_side_effect_event(root=td, kind="health-recompute", payload={"session_id": "s1"})

            status_before = side_effect_queue_status(td, now_ts=100)
            self.assertTrue(status_before.get("ok"))
            self.assertEqual(2, status_before.get("queue_depth"))

            def _stub_processor(*, root: str | Path, kind: str, payload: dict):
                return {"ok": True, "kind": kind, "payload": payload}

            drained = drain_side_effect_queue(root=td, max_items=10, processor=_stub_processor, now_ts=100)
            self.assertTrue(drained.get("ok"))
            self.assertEqual(2, drained.get("processed"))
            self.assertEqual(0, drained.get("failed"))

            status_after = side_effect_queue_status(td, now_ts=100)
            self.assertEqual(0, status_after.get("queue_depth"))

    def test_drain_failure_applies_backoff(self):
        with tempfile.TemporaryDirectory(prefix="cm-se-") as td:
            enqueue_side_effect_event(root=td, kind="neo4j-sync", payload={"session_id": "s1"})

            def _fail_processor(*, root: str | Path, kind: str, payload: dict):
                return {"ok": False, "error": {"code": "boom"}}

            drained = drain_side_effect_queue(root=td, max_items=1, processor=_fail_processor, now_ts=100)
            self.assertTrue(drained.get("ok"))
            self.assertEqual(0, drained.get("processed"))
            self.assertEqual(1, drained.get("failed"))

            q = Path(td) / ".beads" / "events" / "side-effects-queue.json"
            rows = json.loads(q.read_text(encoding="utf-8"))
            self.assertEqual(1, len(rows))
            self.assertGreater(int(rows[0].get("next_retry_at") or 0), 100)

    def test_dreamer_side_effect_writes_candidate_queue(self):
        with tempfile.TemporaryDirectory(prefix="cm-se-") as td:
            with patch("core_memory.runtime.side_effect_queue.dreamer.run_analysis") as ra:
                ra.return_value = [
                    {
                        "source": "b1",
                        "target": "b2",
                        "relationship": "transferable_lesson",
                        "novelty": 0.8,
                        "grounding": 0.9,
                        "confidence": 0.7,
                        "source_title": "s",
                        "target_title": "t",
                    }
                ]
                out = process_side_effect_event(
                    root=td,
                    kind="dreamer-run",
                    payload={"session_id": "s1", "mode": "suggest"},
                )
            self.assertTrue(out.get("ok"))
            cq = out.get("candidate_queue") or {}
            self.assertTrue(cq.get("ok"))
            self.assertEqual(1, cq.get("added"))
            p = Path(td) / ".beads" / "events" / "dreamer-candidates.json"
            self.assertTrue(p.exists())
            rows = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(1, len(rows))
            self.assertEqual("pending", rows[0].get("status"))

    def test_dreamer_mode_off_skips_processing(self):
        with tempfile.TemporaryDirectory(prefix="cm-se-") as td:
            out = process_side_effect_event(
                root=td,
                kind="dreamer-run",
                payload={"mode": "off"},
            )
            self.assertTrue(out.get("ok"))
            self.assertTrue(bool(out.get("skipped")))


if __name__ == "__main__":
    unittest.main()
