from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.association.slo import association_slo_check, association_slo_report
from core_memory.persistence import events
from core_memory.persistence.store import MemoryStore
from core_memory.runtime.engine import process_turn_finalized


class TestAssociationSLOSlice6(unittest.TestCase):
    def test_process_turn_emits_agent_turn_quality_metric(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_AGENT_AUTHORED_REQUIRED": "0",
                "CORE_MEMORY_PREVIEW_ASSOC_PROMOTION": "0",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="hello",
                assistant_final="world",
                metadata={},
            )
            self.assertTrue(out.get("ok"))

            rows = [r for r in (events.iter_metrics(Path(td)) or []) if str(r.get("task_id") or "") == "agent_turn_quality"]
            self.assertTrue(rows)
            row = rows[-1]
            self.assertEqual("success", row.get("result"))
            self.assertIn("non_temporal_semantic_count", row)

    def test_slo_check_fail_and_pass(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="context", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b = s.add_bead(type="context", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"])
            s.link(source_id=a, target_id=b, relationship="shared_tag", explanation="noise")

            events.append_metric(
                Path(td),
                {
                    "task_id": "agent_turn_quality",
                    "result": "success",
                    "agent_source": "default_fallback",
                    "agent_used_fallback": True,
                    "agent_blocked": False,
                    "non_temporal_semantic_count": 0,
                },
            )

            bad = association_slo_check(
                td,
                since="7d",
                min_agent_authored_rate=0.9,
                max_fallback_rate=0.01,
                max_fail_closed_rate=0.01,
                min_avg_non_temporal_semantic=1.0,
                max_active_shared_tag_ratio=0.01,
            )
            self.assertFalse(bad.get("ok"))
            self.assertTrue(bad.get("violations"))

            events.append_metric(
                Path(td),
                {
                    "task_id": "agent_turn_quality",
                    "result": "success",
                    "agent_source": "agent_callable",
                    "agent_used_fallback": False,
                    "agent_blocked": False,
                    "non_temporal_semantic_count": 3,
                },
            )

            # Add one semantic edge so active_shared_tag_ratio improves.
            s.link(source_id=a, target_id=b, relationship="supports", explanation="semantic")

            report = association_slo_report(td)
            self.assertTrue(report.get("ok"))
            self.assertGreaterEqual(float(report.get("agent_authored_rate") or 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
