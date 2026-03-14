import tempfile
import unittest
from pathlib import Path

from core_memory.policy.hygiene import curated_type_title_hygiene
from core_memory.store import MemoryStore


class TestHygieneCurated(unittest.TestCase):
    def test_title_cleanup_and_optional_type_update(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b = s.add_bead(type="context", title="[[reply_to_current]] decision rationale", summary=["metrics evidence"], session_id="main", source_turn_ids=["t1"])
            dry = curated_type_title_hygiene(Path(td), [b], apply=False)
            self.assertTrue(dry.get("ok"))
            self.assertGreaterEqual(int(dry.get("changes", 0)), 1)
            app = curated_type_title_hygiene(Path(td), [b], apply=True)
            self.assertTrue(app.get("ok"))
            idx = s._read_json(s.beads_dir / "index.json")
            bead = (idx.get("beads") or {}).get(b) or {}
            self.assertNotIn("[[reply_to_current]]", str(bead.get("title") or ""))


if __name__ == "__main__":
    unittest.main()
