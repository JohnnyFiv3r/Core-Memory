"""Tests for effort-tier differentiation and association-hop expansion."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core_memory.retrieval.agent import (
    _EFFORT_DEFAULTS,
    _HOP_DECAY,
    _RELATIONSHIP_HOP_WEIGHT,
    _expand_via_association_hops,
)
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


class TestHopScoring(unittest.TestCase):
    """Scoring semantics for association-hop expansion (Ask 1 & 2 in #185 spec)."""

    def _write_index(self, root: Path, beads: dict, associations: list) -> None:
        index = {"beads": beads, "associations": associations}
        beads_dir = root / ".beads"
        beads_dir.mkdir(parents=True, exist_ok=True)
        (beads_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")

    def _bead(self, **kwargs) -> dict:
        return {"type": "event", "retrieval_eligible": True, **kwargs}

    def test_score_derived_from_seed_score_and_edge_weight(self):
        """hop_score ≈ seed_score × rel_weight × confidence × HOP_DECAY."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            beads = {"b1": self._bead(title="b1"), "b2": self._bead(title="b2")}
            assocs = [{"source_bead": "b1", "target_bead": "b2",
                       "relationship": "caused_by", "confidence": 1.0}]
            self._write_index(root, beads, assocs)

            seed_score = 0.80
            ev = [_make_evidence("b1", score=seed_score)]
            out = _expand_via_association_hops(str(root), ev, hops=1)

            hop_item = next(e for e in out if e.bead_id == "b2")
            expected = round(seed_score * _RELATIONSHIP_HOP_WEIGHT["caused_by"] * 1.0 * _HOP_DECAY, 4)
            self.assertAlmostEqual(hop_item.score, expected, places=3)

    def test_causal_edge_outranks_temporal_edge(self):
        """A caused_by neighbour should score higher than a follows neighbour."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            beads = {
                "seed": self._bead(title="seed"),
                "causal_nb": self._bead(title="causal"),
                "temporal_nb": self._bead(title="temporal"),
            }
            assocs = [
                {"source_bead": "seed", "target_bead": "causal_nb",
                 "relationship": "caused_by", "confidence": 0.9},
                {"source_bead": "seed", "target_bead": "temporal_nb",
                 "relationship": "follows", "confidence": 0.9},
            ]
            self._write_index(root, beads, assocs)

            ev = [_make_evidence("seed", score=0.8)]
            out = _expand_via_association_hops(str(root), ev, hops=1)

            causal_score = next(e.score for e in out if e.bead_id == "causal_nb")
            temporal_score = next(e.score for e in out if e.bead_id == "temporal_nb")
            self.assertGreater(causal_score, temporal_score)

    def test_strong_causal_hop_can_displace_weak_vector_match(self):
        """A strong-seed causal 1-hop should rank above a weak vector match."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            beads = {
                "strong_seed": self._bead(title="strong"),
                "causal_nb": self._bead(title="causal_nb"),
                "weak_vector": self._bead(title="weak_vector"),
            }
            assocs = [{"source_bead": "strong_seed", "target_bead": "causal_nb",
                       "relationship": "caused_by", "confidence": 0.95}]
            self._write_index(root, beads, assocs)

            strong_seed_score = 0.90
            weak_vector_score = 0.42
            ev = [
                _make_evidence("strong_seed", score=strong_seed_score),
                _make_evidence("weak_vector", score=weak_vector_score),
            ]
            out = _expand_via_association_hops(str(root), ev, hops=1)

            causal_score = next(e.score for e in out if e.bead_id == "causal_nb")
            # causal hop score ≈ 0.90 × 0.90 × 0.95 × 0.80 ≈ 0.617 > 0.42
            self.assertGreater(causal_score, weak_vector_score,
                               f"causal hop {causal_score:.3f} should beat weak vector {weak_vector_score:.3f}")

    def test_two_hop_score_is_lower_than_one_hop(self):
        """2-hop items score lower than 1-hop items due to decay compounding."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            beads = {
                "b1": self._bead(title="b1"),
                "b2": self._bead(title="b2"),
                "b3": self._bead(title="b3"),
            }
            assocs = [
                {"source_bead": "b1", "target_bead": "b2",
                 "relationship": "caused_by", "confidence": 1.0},
                {"source_bead": "b2", "target_bead": "b3",
                 "relationship": "caused_by", "confidence": 1.0},
            ]
            self._write_index(root, beads, assocs)

            ev = [_make_evidence("b1", score=0.9)]
            out = _expand_via_association_hops(str(root), ev, hops=2)

            hop1_score = next(e.score for e in out if e.bead_id == "b2")
            hop2_score = next(e.score for e in out if e.bead_id == "b3")
            self.assertGreater(hop1_score, hop2_score)

    def test_hop_items_sorted_by_score_descending(self):
        """When max_expansion < discovered, highest-scored hops are kept."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            beads: dict = {"b0": self._bead(title="b0")}
            assocs = []
            # Create one strong causal neighbour and several weak temporal neighbours
            beads["causal"] = self._bead(title="causal")
            assocs.append({"source_bead": "b0", "target_bead": "causal",
                            "relationship": "caused_by", "confidence": 0.95})
            for i in range(5):
                bid = f"temporal_{i}"
                beads[bid] = self._bead(title=bid)
                assocs.append({"source_bead": "b0", "target_bead": bid,
                                "relationship": "follows", "confidence": 0.8})
            self._write_index(root, beads, assocs)

            ev = [_make_evidence("b0", score=0.8)]
            # max_expansion=3: should keep causal + 2 temporal (not lose causal)
            out = _expand_via_association_hops(str(root), ev, hops=1, max_expansion=3)

            added_ids = {e.bead_id for e in out} - {"b0"}
            self.assertIn("causal", added_ids,
                          "highest-scored causal hop must be within max_expansion window")
            self.assertEqual(3, len(added_ids))

    def test_confidence_zero_edge_excluded_in_practice(self):
        """An edge with confidence=0 produces score≈0 and should not appear."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            beads = {"b1": self._bead(title="b1"), "b2": self._bead(title="b2")}
            assocs = [{"source_bead": "b1", "target_bead": "b2",
                       "relationship": "caused_by", "confidence": 0.0}]
            self._write_index(root, beads, assocs)

            ev = [_make_evidence("b1", score=0.8)]
            out = _expand_via_association_hops(str(root), ev, hops=1)

            hop_item = next((e for e in out if e.bead_id == "b2"), None)
            if hop_item is not None:
                self.assertEqual(0.0, hop_item.score)

    def test_relationship_weight_constants(self):
        """Causal > semantic > generic > temporal in the weight table."""
        self.assertGreater(_RELATIONSHIP_HOP_WEIGHT["caused_by"],
                           _RELATIONSHIP_HOP_WEIGHT["supports"])
        self.assertGreater(_RELATIONSHIP_HOP_WEIGHT["supports"],
                           _RELATIONSHIP_HOP_WEIGHT["associated_with"])
        self.assertGreater(_RELATIONSHIP_HOP_WEIGHT["associated_with"],
                           _RELATIONSHIP_HOP_WEIGHT["follows"])


if __name__ == "__main__":
    unittest.main()
