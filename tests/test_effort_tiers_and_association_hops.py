"""Tests for effort-tier differentiation and association-hop expansion."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core_memory.retrieval.agent import _EFFORT_DEFAULTS, _expand_via_association_hops
from core_memory.retrieval.contracts import EvidenceItem


def _make_evidence(bead_id: str, score: float = 0.8) -> EvidenceItem:
    return EvidenceItem(bead_id=bead_id, type="event", title=bead_id, content_excerpt="", score=score, reason="vector")


class TestEffortTierDefaults(unittest.TestCase):
    def test_k_increases_monotonically(self):
        assert _EFFORT_DEFAULTS["low"]["k"] < _EFFORT_DEFAULTS["medium"]["k"] < _EFFORT_DEFAULTS["high"]["k"]

    def test_association_hops_keys_present(self):
        for tier in ("low", "medium", "high"):
            self.assertIn("association_hops", _EFFORT_DEFAULTS[tier])

    def test_low_has_zero_hops(self):
        self.assertEqual(0, _EFFORT_DEFAULTS["low"]["association_hops"])

    def test_medium_has_one_hop(self):
        self.assertEqual(1, _EFFORT_DEFAULTS["medium"]["association_hops"])

    def test_high_has_two_hops(self):
        self.assertEqual(2, _EFFORT_DEFAULTS["high"]["association_hops"])

    def test_medium_k_is_12(self):
        self.assertEqual(12, _EFFORT_DEFAULTS["medium"]["k"])

    def test_high_k_is_20(self):
        self.assertEqual(20, _EFFORT_DEFAULTS["high"]["k"])


class TestExpandViaAssociationHops(unittest.TestCase):
    def _write_index(self, root: Path, beads: dict, associations: list) -> None:
        index = {"beads": beads, "associations": associations}
        beads_dir = root / ".beads"
        beads_dir.mkdir(parents=True, exist_ok=True)
        (beads_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")

    def test_zero_hops_returns_same_list(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_index(root, {}, [])
            ev = [_make_evidence("b1")]
            out = _expand_via_association_hops(str(root), ev, hops=0)
            self.assertIs(ev, out)

    def test_no_evidence_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_index(root, {}, [])
            out = _expand_via_association_hops(str(root), [], hops=1)
            self.assertEqual([], out)

    def test_missing_index_returns_original(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".beads").mkdir()
            ev = [_make_evidence("b1")]
            out = _expand_via_association_hops(str(root), ev, hops=1)
            self.assertIs(ev, out)

    def test_one_hop_adds_direct_neighbour(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            beads = {
                "b1": {"type": "event", "title": "b1", "retrieval_eligible": True, "summary": ["b1 text"]},
                "b2": {"type": "event", "title": "b2", "retrieval_eligible": True, "summary": ["b2 text"]},
            }
            assocs = [{"source_bead": "b1", "target_bead": "b2", "relationship": "caused_by"}]
            self._write_index(root, beads, assocs)

            ev = [_make_evidence("b1", score=0.8)]
            out = _expand_via_association_hops(str(root), ev, hops=1)

            bead_ids = {e.bead_id for e in out}
            self.assertIn("b1", bead_ids)
            self.assertIn("b2", bead_ids)
            self.assertEqual(2, len(out))

    def test_hop_score_below_seed_score(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            beads = {
                "b1": {"type": "event", "title": "b1", "retrieval_eligible": True},
                "b2": {"type": "event", "title": "b2", "retrieval_eligible": True},
            }
            assocs = [{"source_bead": "b1", "target_bead": "b2"}]
            self._write_index(root, beads, assocs)

            ev = [_make_evidence("b1", score=0.8)]
            out = _expand_via_association_hops(str(root), ev, hops=1)

            hop_item = next(e for e in out if e.bead_id == "b2")
            self.assertLess(hop_item.score, 0.8)
            self.assertEqual("association_hop", hop_item.reason)

    def test_two_hops_adds_second_degree_neighbour(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            beads = {
                "b1": {"type": "event", "title": "b1", "retrieval_eligible": True},
                "b2": {"type": "event", "title": "b2", "retrieval_eligible": True},
                "b3": {"type": "event", "title": "b3", "retrieval_eligible": True},
            }
            assocs = [
                {"source_bead": "b1", "target_bead": "b2"},
                {"source_bead": "b2", "target_bead": "b3"},
            ]
            self._write_index(root, beads, assocs)

            ev = [_make_evidence("b1", score=0.8)]
            out = _expand_via_association_hops(str(root), ev, hops=2)

            bead_ids = {e.bead_id for e in out}
            self.assertEqual({"b1", "b2", "b3"}, bead_ids)

    def test_one_hop_does_not_reach_second_degree(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            beads = {
                "b1": {"type": "event", "title": "b1", "retrieval_eligible": True},
                "b2": {"type": "event", "title": "b2", "retrieval_eligible": True},
                "b3": {"type": "event", "title": "b3", "retrieval_eligible": True},
            }
            assocs = [
                {"source_bead": "b1", "target_bead": "b2"},
                {"source_bead": "b2", "target_bead": "b3"},
            ]
            self._write_index(root, beads, assocs)

            ev = [_make_evidence("b1", score=0.8)]
            out = _expand_via_association_hops(str(root), ev, hops=1)

            bead_ids = {e.bead_id for e in out}
            self.assertIn("b2", bead_ids)
            self.assertNotIn("b3", bead_ids)

    def test_non_retrieval_eligible_bead_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            beads = {
                "b1": {"type": "event", "title": "b1", "retrieval_eligible": True},
                "b2": {"type": "event", "title": "b2", "retrieval_eligible": False},
            }
            assocs = [{"source_bead": "b1", "target_bead": "b2"}]
            self._write_index(root, beads, assocs)

            ev = [_make_evidence("b1", score=0.8)]
            out = _expand_via_association_hops(str(root), ev, hops=1)

            bead_ids = {e.bead_id for e in out}
            self.assertNotIn("b2", bead_ids)

    def test_seeds_not_duplicated(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            beads = {
                "b1": {"type": "event", "title": "b1", "retrieval_eligible": True},
                "b2": {"type": "event", "title": "b2", "retrieval_eligible": True},
            }
            assocs = [{"source_bead": "b1", "target_bead": "b2"}]
            self._write_index(root, beads, assocs)

            ev = [_make_evidence("b1"), _make_evidence("b2")]
            out = _expand_via_association_hops(str(root), ev, hops=1)

            bead_ids = [e.bead_id for e in out]
            self.assertEqual(len(bead_ids), len(set(bead_ids)), "seeds must not appear twice")

    def test_max_expansion_caps_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            beads: dict = {"b0": {"type": "event", "title": "b0", "retrieval_eligible": True}}
            assocs = []
            for i in range(1, 25):
                bid = f"b{i}"
                beads[bid] = {"type": "event", "title": bid, "retrieval_eligible": True}
                assocs.append({"source_bead": "b0", "target_bead": bid})
            self._write_index(root, beads, assocs)

            ev = [_make_evidence("b0")]
            out = _expand_via_association_hops(str(root), ev, hops=1, max_expansion=5)

            self.assertEqual(1 + 5, len(out))


if __name__ == "__main__":
    unittest.main()
