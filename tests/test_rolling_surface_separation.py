import tempfile
import unittest
from pathlib import Path

from core_memory.store import MemoryStore
from core_memory.rolling_surface import (
    _load_filtered_beads,
    _select_beads_for_budget,
    _build_surface_payload,
    render_rolling_text,
)


class TestRollingSurfaceSeparation(unittest.TestCase):
    def test_selection_payload_and_render_are_separable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "memory"
            s = MemoryStore(str(root))
            s.add_bead(type="context", title="A", summary=["one"], session_id="main", source_turn_ids=["t1"])
            s.add_bead(type="decision", title="B", summary=["two"], session_id="main", source_turn_ids=["t2"])

            filtered, excluded_superseded = _load_filtered_beads(str(root))
            included, total = _select_beads_for_budget(filtered, token_budget=500, max_beads=20)
            meta, included_ids, excluded_ids = _build_surface_payload(
                filtered=filtered,
                included=included,
                token_budget=500,
                max_beads=20,
                excluded_superseded_count=len(excluded_superseded),
                token_estimate=total,
            )
            text = render_rolling_text(included)

            self.assertTrue(included)
            self.assertIn("records", meta)
            self.assertGreaterEqual(len(included_ids), 1)
            self.assertIsInstance(excluded_ids, list)
            self.assertIn("#", text)


if __name__ == "__main__":
    unittest.main()
