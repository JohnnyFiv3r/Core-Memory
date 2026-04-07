from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory import dreamer
from core_memory.persistence.store import MemoryStore


class TestDreamerAnalysisSlice62A(unittest.TestCase):
    def _seed_two_beads(self, root: str) -> None:
        s = MemoryStore(root)
        s.add_bead(type="decision", title="A", summary=["always do retries"], session_id="s1", source_turn_ids=["t1"], tags=["alpha"], scope="project")
        s.add_bead(type="lesson", title="B", summary=["generally apply this lesson"], session_id="s2", source_turn_ids=["t2"], tags=["beta"], scope="global")

    def test_novel_only_dedupes_previously_seen_pairs(self):
        with tempfile.TemporaryDirectory(prefix="cm-dream-") as td:
            self._seed_two_beads(td)
            s = MemoryStore(td)
            with patch("core_memory.dreamer.compute_distance", return_value=0.9), patch(
                "core_memory.dreamer.score_association",
                return_value={"relationship": "structural_symmetry", "novelty": 0.8, "confidence": 0.8, "grounding": 0.9},
            ):
                first = dreamer.run_analysis(store=s, novel_only=False)
                self.assertTrue(isinstance(first, list) and len(first) >= 1)

                second = dreamer.run_analysis(store=s, novel_only=True)
                self.assertEqual("no_associations", (second[0] or {}).get("status"))

    def test_max_exposure_blocks_overexposed_pairs(self):
        with tempfile.TemporaryDirectory(prefix="cm-dream-") as td:
            self._seed_two_beads(td)
            s = MemoryStore(td)
            with patch("core_memory.dreamer.compute_distance", return_value=0.9), patch(
                "core_memory.dreamer.score_association",
                return_value={"relationship": "structural_symmetry", "novelty": 0.8, "confidence": 0.8, "grounding": 0.9},
            ):
                first = dreamer.run_analysis(store=s, novel_only=False)
                self.assertTrue(isinstance(first, list) and len(first) >= 1)

                second = dreamer.run_analysis(store=s, novel_only=False, max_exposure=0)
                self.assertEqual("no_associations", (second[0] or {}).get("status"))

    def test_seen_window_runs_filters_old_seen_pairs(self):
        with tempfile.TemporaryDirectory(prefix="cm-dream-") as td:
            p = Path(td) / ".beads" / "events" / "dreamer-seen.jsonl"
            p.parent.mkdir(parents=True, exist_ok=True)
            rows = [
                {"run_id": "r1", "pair_key": "a::b", "source": "a", "target": "b"},
                {"run_id": "r2", "pair_key": "c::d", "source": "c", "target": "d"},
            ]
            p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

            seen_all, _exp_all = dreamer._load_seen_state(p, seen_window_runs=0)
            seen_last, _exp_last = dreamer._load_seen_state(p, seen_window_runs=1)
            self.assertIn("a::b", seen_all)
            self.assertIn("c::d", seen_all)
            self.assertNotIn("a::b", seen_last)
            self.assertIn("c::d", seen_last)

    def test_relationship_heuristics_emit_contradiction_and_generalization(self):
        b1 = {"summary": ["we should not do this"], "title": "A", "type": "decision"}
        b2 = {"summary": ["yes we always do this"], "title": "B", "type": "lesson"}
        out1 = dreamer.score_association(b1, b2, 0.8)
        self.assertEqual("contradicts", out1.get("relationship"))

        b3 = {"summary": ["always enforce strict retries"], "title": "C", "type": "decision"}
        b4 = {"summary": ["generally this works"], "title": "D", "type": "lesson"}
        out2 = dreamer.score_association(b3, b4, 0.8)
        self.assertEqual("generalizes", out2.get("relationship"))


if __name__ == "__main__":
    unittest.main()
