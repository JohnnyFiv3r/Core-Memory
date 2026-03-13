import tempfile
import unittest
from pathlib import Path

from core_memory.cli import _canonical_health_report


class TestCliCanonicalHealth(unittest.TestCase):
    def test_canonical_health_report_green(self):
        with tempfile.TemporaryDirectory() as td:
            out = _canonical_health_report(td)
            self.assertTrue(out.get("ok"))
            self.assertTrue(out.get("all_green"))
            checks = out.get("checks") or {}
            for key in [
                "turn_path",
                "flush_once_per_cycle",
                "rolling_window_maintenance",
                "archive_ergonomics",
                "retrieval_path",
            ]:
                self.assertIn(key, checks)
                self.assertTrue(bool(checks.get(key)))

    def test_canonical_health_report_write(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "canonical-health.json"
            out = _canonical_health_report(td, write_path=str(p))
            self.assertTrue(p.exists())
            self.assertEqual(str(p), out.get("written"))


if __name__ == "__main__":
    unittest.main()
