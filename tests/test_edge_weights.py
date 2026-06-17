"""Tests for graph/edge_weights.py — canonical traversal scoring shared across backends."""
from __future__ import annotations

import unittest

from core_memory.graph.edge_weights import (
    DEFAULT_HOP_WEIGHT,
    DEFAULT_PROVENANCE_FACTOR,
    DIRECTIONAL_RELS,
    HOP_DECAY,
    PROVENANCE_FACTOR,
    RELATIONSHIP_HOP_WEIGHT,
    REVERSE_DIRECTION_FACTOR,
    normalize_backend_chain,
    score_edge,
)


class TestScoreEdge(unittest.TestCase):
    def test_known_causal_rel_uses_high_weight(self):
        sc = score_edge("causes", confidence=1.0, provenance="agent_judged")
        expected = RELATIONSHIP_HOP_WEIGHT["causes"] * 1.0 * PROVENANCE_FACTOR["agent_judged"]
        self.assertAlmostEqual(sc, expected, places=6)

    def test_unknown_rel_uses_default_weight(self):
        sc = score_edge("totally_unknown", confidence=1.0, provenance="agent_judged")
        expected = DEFAULT_HOP_WEIGHT * 1.0 * PROVENANCE_FACTOR["agent_judged"]
        self.assertAlmostEqual(sc, expected, places=6)

    def test_temporal_rel_scores_low(self):
        sc_causal = score_edge("causes", confidence=1.0, provenance="agent_judged")
        sc_temporal = score_edge("follows", confidence=1.0, provenance="agent_judged")
        self.assertGreater(sc_causal, sc_temporal)

    def test_model_inferred_lower_than_agent_judged(self):
        sc_agent = score_edge("causes", confidence=1.0, provenance="agent_judged")
        sc_model = score_edge("causes", confidence=1.0, provenance="model_inferred")
        self.assertGreater(sc_agent, sc_model)

    def test_preview_classifier_lower_than_model_inferred(self):
        sc_model = score_edge("causes", confidence=1.0, provenance="model_inferred")
        sc_preview = score_edge("causes", confidence=1.0, provenance="preview_classifier")
        self.assertGreater(sc_model, sc_preview)

    def test_unknown_provenance_uses_default_factor(self):
        sc = score_edge("causes", confidence=1.0, provenance="totally_unknown")
        expected = RELATIONSHIP_HOP_WEIGHT["causes"] * 1.0 * DEFAULT_PROVENANCE_FACTOR
        self.assertAlmostEqual(sc, expected, places=6)

    def test_low_trust_provenance_overrides_edge_class(self):
        # The crawler stamps edge_class="agent_judged" on every appended edge
        # (channel marker). When the relationship label itself came from the
        # preview classifier, the low-trust discount must still apply —
        # otherwise the provenance-aware weighting is masked for all new edges.
        sc = score_edge("causes", confidence=1.0,
                        provenance="preview_classifier", edge_class="agent_judged")
        sc_preview = score_edge("causes", confidence=1.0, provenance="preview_classifier")
        self.assertAlmostEqual(sc, sc_preview, places=6)

    def test_edge_class_overrides_normal_provenance(self):
        # Non-low-trust provenances: the channel marker still wins.
        sc = score_edge("causes", confidence=1.0,
                        provenance="model_inferred", edge_class="agent_judged")
        sc_agent = score_edge("causes", confidence=1.0, provenance="agent_judged")
        self.assertAlmostEqual(sc, sc_agent, places=6)

    def test_confidence_scales_score(self):
        sc_full = score_edge("causes", confidence=1.0, provenance="agent_judged")
        sc_half = score_edge("causes", confidence=0.5, provenance="agent_judged")
        self.assertAlmostEqual(sc_half, sc_full * 0.5, places=6)

    def test_confidence_clamped_to_unit_range(self):
        sc_over = score_edge("causes", confidence=1.5, provenance="agent_judged")
        sc_one = score_edge("causes", confidence=1.0, provenance="agent_judged")
        self.assertAlmostEqual(sc_over, sc_one, places=6)

    def test_zero_confidence_yields_zero(self):
        sc = score_edge("causes", confidence=0.0, provenance="agent_judged")
        self.assertEqual(0.0, sc)


class TestConstants(unittest.TestCase):
    def test_causal_weights_higher_than_temporal(self):
        self.assertGreater(RELATIONSHIP_HOP_WEIGHT["causes"], RELATIONSHIP_HOP_WEIGHT["follows"])

    def test_semantic_weights_between_causal_and_temporal(self):
        self.assertGreater(RELATIONSHIP_HOP_WEIGHT["supports"], RELATIONSHIP_HOP_WEIGHT["follows"])
        self.assertLess(RELATIONSHIP_HOP_WEIGHT["supports"], RELATIONSHIP_HOP_WEIGHT["causes"])

    def test_hop_decay_is_subunit(self):
        self.assertGreater(HOP_DECAY, 0.0)
        self.assertLess(HOP_DECAY, 1.0)

    def test_directional_rels_includes_causal_edges(self):
        for rel in ("causes", "leads_to", "enables", "supersedes"):
            self.assertIn(rel, DIRECTIONAL_RELS)

    def test_reverse_factor_is_subunit(self):
        self.assertGreater(REVERSE_DIRECTION_FACTOR, 0.0)
        self.assertLess(REVERSE_DIRECTION_FACTOR, 1.0)

    def test_agent_aliases_from_retrieval_agent_unchanged(self):
        from core_memory.retrieval.agent import (
            _HOP_DECAY,
            _PROVENANCE_FACTOR,
            _RELATIONSHIP_HOP_WEIGHT,
        )
        self.assertIs(_RELATIONSHIP_HOP_WEIGHT, RELATIONSHIP_HOP_WEIGHT)
        self.assertIs(_PROVENANCE_FACTOR, PROVENANCE_FACTOR)
        self.assertEqual(_HOP_DECAY, HOP_DECAY)


class TestNormalizeBackendChain(unittest.TestCase):
    def _make_backend_chain(self, nodes=None, edges=None, score=None):
        chain = {}
        if nodes is not None:
            chain["nodes"] = nodes
        if edges is not None:
            chain["edges"] = edges
        if score is not None:
            chain["score"] = score
        return chain

    def test_path_extracted_from_nodes(self):
        chain = self._make_backend_chain(
            nodes=[{"id": "bead-AAAAAAAAAAAA", "type": "event", "title": "A"},
                   {"id": "bead-BBBBBBBBBBBB", "type": "decision", "title": "B"}],
            edges=[{"rel": "causes", "src": "bead-AAAAAAAAAAAA", "tgt": "bead-BBBBBBBBBBBB"}],
        )
        out = normalize_backend_chain(chain)
        self.assertEqual(["bead-AAAAAAAAAAAA", "bead-BBBBBBBBBBBB"], out["path"])

    def test_tgt_renamed_to_dst(self):
        chain = self._make_backend_chain(
            nodes=[{"id": "b1"}, {"id": "b2"}],
            edges=[{"rel": "causes", "src": "b1", "tgt": "b2"}],
        )
        out = normalize_backend_chain(chain)
        edge = out["edges"][0]
        self.assertIn("dst", edge)
        self.assertNotIn("tgt", edge)
        self.assertEqual("b2", edge["dst"])

    def test_dst_preserved_when_already_present(self):
        chain = self._make_backend_chain(
            nodes=[{"id": "b1"}, {"id": "b2"}],
            edges=[{"rel": "causes", "src": "b1", "dst": "b2"}],
        )
        out = normalize_backend_chain(chain)
        edge = out["edges"][0]
        self.assertIn("dst", edge)
        self.assertEqual("b2", edge["dst"])

    def test_score_computed_from_edges_when_absent(self):
        chain = self._make_backend_chain(
            nodes=[{"id": "b1"}, {"id": "b2"}],
            edges=[{"rel": "causes", "src": "b1", "tgt": "b2", "confidence": 0.9}],
        )
        out = normalize_backend_chain(chain)
        expected = score_edge("causes", confidence=0.9, provenance="model_inferred")
        self.assertAlmostEqual(float(out["score"]), expected, places=5)

    def test_existing_score_preserved(self):
        chain = self._make_backend_chain(
            nodes=[{"id": "b1"}, {"id": "b2"}],
            edges=[{"rel": "causes", "src": "b1", "tgt": "b2"}],
            score=0.999,
        )
        out = normalize_backend_chain(chain)
        self.assertAlmostEqual(0.999, float(out["score"]), places=6)

    def test_causal_chain_scores_higher_than_temporal(self):
        causal = normalize_backend_chain({
            "nodes": [{"id": "b1"}, {"id": "b2"}],
            "edges": [{"rel": "causes", "src": "b1", "tgt": "b2", "confidence": 0.9}],
        })
        temporal = normalize_backend_chain({
            "nodes": [{"id": "c1"}, {"id": "c2"}],
            "edges": [{"rel": "follows", "src": "c1", "tgt": "c2", "confidence": 0.9}],
        })
        self.assertGreater(float(causal["score"]), float(temporal["score"]))

    def test_empty_nodes_yields_empty_path(self):
        chain = self._make_backend_chain(nodes=[], edges=[])
        out = normalize_backend_chain(chain)
        self.assertEqual([], out["path"])

    def test_empty_edges_yields_zero_score(self):
        chain = self._make_backend_chain(nodes=[{"id": "b1"}], edges=[])
        out = normalize_backend_chain(chain)
        self.assertEqual(0.0, float(out.get("score") or 0.0))

    def test_existing_path_not_overwritten(self):
        chain = {
            "path": ["already-set-1", "already-set-2"],
            "nodes": [{"id": "should-be-ignored"}],
            "edges": [],
            "score": 0.5,
        }
        out = normalize_backend_chain(chain)
        self.assertEqual(["already-set-1", "already-set-2"], out["path"])

    def test_extra_keys_preserved(self):
        chain = self._make_backend_chain(
            nodes=[{"id": "b1"}, {"id": "b2"}],
            edges=[{"rel": "causes", "src": "b1", "tgt": "b2"}],
        )
        chain["backend"] = "neo4j"
        chain["custom_diag"] = {"latency_ms": 42}
        out = normalize_backend_chain(chain)
        self.assertEqual("neo4j", out["backend"])
        self.assertEqual({"latency_ms": 42}, out["custom_diag"])

    def test_confidence_default_used_when_missing(self):
        chain = self._make_backend_chain(
            nodes=[{"id": "b1"}, {"id": "b2"}],
            edges=[{"rel": "causes", "src": "b1", "tgt": "b2"}],  # no confidence
        )
        out = normalize_backend_chain(chain)
        expected = score_edge("causes", confidence=0.85)  # default
        self.assertAlmostEqual(float(out["score"]), expected, places=5)

    def test_multi_edge_chain_score_is_mean(self):
        chain = self._make_backend_chain(
            nodes=[{"id": "b1"}, {"id": "b2"}, {"id": "b3"}],
            edges=[
                {"rel": "causes", "src": "b1", "tgt": "b2", "confidence": 0.9},
                {"rel": "follows", "src": "b2", "tgt": "b3", "confidence": 0.9},
            ],
        )
        out = normalize_backend_chain(chain)
        e1 = score_edge("causes", confidence=0.9)
        e2 = score_edge("follows", confidence=0.9)
        expected = (e1 + e2) / 2
        self.assertAlmostEqual(float(out["score"]), expected, places=5)


if __name__ == "__main__":
    unittest.main()
