from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.association.crawler_contract import _crawler_updates_log_path, merge_crawler_updates
from core_memory.persistence.io_utils import append_jsonl
from core_memory.persistence.store import MemoryStore
from core_memory.runtime.engine import _queue_preview_associations


class TestAssociationQualityPolicySlice4(unittest.TestCase):
    def test_preview_promotion_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(type="context", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"], tags=["tag-a"])
            b2 = s.add_bead(type="context", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"], tags=["tag-a"])
            queued = _queue_preview_associations(td, "s1", [b1, b2])
            self.assertEqual(0, queued)

    def test_preview_shared_tag_filtered_when_not_allowed(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_PREVIEW_ASSOC_PROMOTION": "1",
                "CORE_MEMORY_PREVIEW_ASSOC_ALLOW_SHARED_TAG": "0",
            },
            clear=False,
        ):
            s = MemoryStore(td)
            b1 = s.add_bead(type="context", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"], tags=["tag-a"])
            b2 = s.add_bead(type="context", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"], tags=["tag-a"])
            queued = _queue_preview_associations(td, "s1", [b1, b2])
            self.assertEqual(0, queued)

    def test_merge_quarantines_noncanonical_preview_append(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="context", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b = s.add_bead(type="context", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"])

            log_path = _crawler_updates_log_path(td, "s1")
            append_jsonl(
                log_path,
                {
                    "schema": "openclaw.memory.crawler_update.v1",
                    "kind": "association_append",
                    "session_id": "s1",
                    "id": "assoc-test",
                    "source_bead": a,
                    "target_bead": b,
                    "relationship": "shared_tag",
                    "edge_class": "preview_promoted",
                    "confidence": 0.9,
                    "reason_text": "shared tag",
                    "provenance": "model_inferred",
                },
            )

            out = merge_crawler_updates(td, "s1")
            self.assertTrue(out.get("ok"))
            self.assertEqual(0, int(out.get("associations_appended") or 0))
            self.assertEqual(1, int(out.get("associations_quarantined") or 0))

            qpath = Path(out.get("quarantine_path") or "")
            self.assertTrue(qpath.exists())
            rows = [json.loads(x) for x in qpath.read_text(encoding="utf-8").splitlines() if x.strip()]
            self.assertTrue(rows)


if __name__ == "__main__":
    unittest.main()
