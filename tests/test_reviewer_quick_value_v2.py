from __future__ import annotations

import tempfile
import unittest

import core_memory.runtime.reviewer_quick_value as rqv
from core_memory.runtime.reviewer_quick_value import reviewer_quick_value_v2


class TestReviewerQuickValueV2Slice66A(unittest.TestCase):
    def test_report_contains_required_demo_steps(self):
        with tempfile.TemporaryDirectory(prefix="cm-rqv2-") as td:
            out = reviewer_quick_value_v2(td)

            self.assertEqual("core_memory.reviewer_quick_value_v2.v1", out.get("schema"))
            steps = out.get("steps") or {}
            self.assertIn("canonical_write", steps)
            self.assertIn("retrieval", steps)
            self.assertIn("repeated_incident_improvement", steps)
            self.assertIn("dreamer_transfer_improvement", steps)

            self.assertTrue(bool((steps.get("canonical_write") or {}).get("ok")))
            self.assertTrue(bool((steps.get("retrieval") or {}).get("improved")))
            self.assertTrue(bool((steps.get("repeated_incident_improvement") or {}).get("improved")))
            self.assertTrue(bool((steps.get("dreamer_transfer_improvement") or {}).get("improved")))

            overall = out.get("overall") or {}
            self.assertTrue(bool(overall.get("quick_value_passed")))

    def test_dreamer_seed_path_avoids_direct_store_add_bead_writes(self):
        self.assertFalse(hasattr(rqv, "MemoryStore"))


if __name__ == "__main__":
    unittest.main()
